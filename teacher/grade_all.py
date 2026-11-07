"""Grade a whole class: every student repo of an assignment, or a folder of exported submissions.

    python teacher/grade_all.py ORG PREFIX            # repos named PREFIX*, for example hw1-
    python teacher/grade_all.py --local DIR           # DIR/<student>/<student file>, one folder each

It reads the assignment (compiler, student file, tests, pass mark) from autograding/tests.json, the file
the student repos were created from, and grades each student with the same code as autograding/grade.py.
Results go to grades.csv. GitHub mode needs the GitHub CLI (gh), signed in with access to the organization's
repos. Both modes need ONLINECOMPILER_API_KEY.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "autograding"))

import grade as grader  # noqa: E402  (the same grading code the student repos run)

LIST_LIMIT = 1000
RUNS_PER_MINUTE = 30  # the Free plan's per-minute limit; paced under it by default


def gh(*args: str) -> str:
    """Run the GitHub CLI and return stdout; raises RuntimeError with gh's message when it fails."""
    try:
        done = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        raise RuntimeError("The GitHub CLI (gh) is not installed. See https://cli.github.com/") from None
    except subprocess.TimeoutExpired:
        raise RuntimeError("gh timed out") from None
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout).strip() or f"gh exited {done.returncode}")
    return done.stdout


def list_repos(org: str, prefix: str, run: Callable[..., str] = gh) -> list[dict[str, Any]]:
    out = run("repo", "list", org, "--json", "name,isArchived,pushedAt", "--limit", str(LIST_LIMIT))
    repos = [r for r in json.loads(out) if r["name"].startswith(prefix)]
    if len(repos) >= LIST_LIMIT:
        print(f"Warning: gh returned {LIST_LIMIT} repositories, the most it was asked for; some may be missing.",
              file=sys.stderr)
    return sorted(repos, key=lambda r: r["name"])


def fetch_submission(org: str, repo: str, path: str, run: Callable[..., str] = gh) -> tuple[str, str]:
    """The student file at the head of the default branch: (commit sha, text)."""
    sha = run("api", f"repos/{org}/{repo}/commits/HEAD", "--jq", ".sha").strip()
    if not sha:
        raise RuntimeError("the repository has no commits")
    try:
        body = json.loads(run("api", f"repos/{org}/{repo}/contents/{path}?ref={sha}"))
    except RuntimeError as error:
        if "Not Found" in str(error) or "404" in str(error):
            raise RuntimeError(f"{path} not found in the repository") from None
        raise
    if isinstance(body, list) or body.get("encoding") != "base64":
        raise RuntimeError(f"{path} is not a regular file")
    return sha, base64.b64decode(body["content"]).decode("utf-8", "replace")


def local_submissions(directory: Path, path: str) -> list[dict[str, Any]]:
    if not directory.is_dir():
        raise RuntimeError(f"{directory} is not a folder")
    return [{"name": d.name, "path": d / path} for d in sorted(directory.iterdir())
            if d.is_dir() and not d.name.startswith(".")]


def student_of(repo: str, prefix: str) -> str:
    login = repo[len(prefix):] if repo.startswith(prefix) else repo
    return login.lstrip("-_") or repo


def safe_cell(value: Any) -> Any:
    """Keep a spreadsheet from running a student-chosen text that starts like a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


def column_names(config: dict[str, Any]) -> list[str]:
    names, hidden = [], 0
    for test in config["tests"]:
        if test["hidden"]:
            hidden += 1
            names.append(f"Hidden test {hidden}")
        else:
            names.append(test["name"])
    return names


def grade_one(config: dict[str, Any], code: str, client: Any, sleep: Callable[[float], None]) -> dict[str, Any]:
    return grader.grade_code(config, code, client, retry_rate_limit=True, sleep=sleep)


def result_row(config: dict[str, Any], repo: str, student: str, sha: str, code: str | None, error: str,
               client: Any, sleep: Callable[[float], None]) -> dict[str, Any]:
    row: dict[str, Any] = {"repo": repo, "student": student, "commit": sha, "score": "", "passed": "", "error": error}
    for name in column_names(config):
        row[name] = ""
    if code is None:
        return row
    try:
        grade = grade_one(config, code, client, sleep)
    except grader.GradeError as problem:
        row["error"] = str(problem)
        return row
    row["score"] = f"{grade['score']:g}"
    row["passed"] = "yes" if grade["passed"] else "no"
    for name, test in zip(column_names(config), grade["tests"]):
        row[name] = test["verdict"]
    if all(t["verdict"] in ("runtime_error", "compile_error") for t in grade["tests"]):
        row["error"] = grade["first_error"] or grade["tests"][0]["verdict"]
    return row


def write_csv(path: str | Path, config: dict[str, Any], rows: list[dict[str, Any]]) -> None:
    fields = ["repo", "student", "commit", "score", "passed", *column_names(config), "error"]
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: safe_cell(row.get(key, "")) for key in fields})


def main(argv: list[str] | None = None, client: Any = None, run: Callable[..., str] = gh,
         sleep: Callable[[float], None] = time.sleep) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("org", nargs="?", help="GitHub organization")
    parser.add_argument("prefix", nargs="?", default="", help="repository name prefix, for example hw1-")
    parser.add_argument("--local", metavar="DIR", help="grade DIR/<student>/<student file> instead of GitHub")
    parser.add_argument("--config", default=str(ROOT / "autograding" / "tests.json"), help="path to tests.json")
    parser.add_argument("--out", default="grades.csv", help="CSV to write (default grades.csv)")
    parser.add_argument("--pause", type=float, help="seconds between students (default: paced under the Free "
                                                    f"plan's {RUNS_PER_MINUTE} runs a minute)")
    args = parser.parse_args(argv)
    if not args.local and not (args.org and args.prefix):
        parser.error("give ORG and PREFIX, or --local DIR")
    try:
        config = grader.load_config(args.config)
        client = client or grader.make_client()
        if args.local:
            entries = local_submissions(Path(args.local), config["file"])
        else:
            entries = list_repos(args.org, args.prefix, run)
    except (grader.ConfigError, grader.GradeError, RuntimeError) as error:
        print(f"Cannot start: {error}", file=sys.stderr)
        return 2
    if not entries:
        print("No submissions found.", file=sys.stderr)
        return 2
    pause = args.pause if args.pause is not None else len(config["tests"]) * 60 / RUNS_PER_MINUTE
    rows = []
    for index, entry in enumerate(entries):
        sha, code, error = "", None, ""
        if args.local:
            repo = student = entry["name"]
            try:
                code = grader.read_code(entry["path"])
            except grader.GradeError as problem:
                error = str(problem)
        else:
            repo, student = entry["name"], student_of(entry["name"], args.prefix)
            try:
                sha, code = fetch_submission(args.org, repo, config["file"], run)
            except (RuntimeError, ValueError) as problem:
                error = str(problem)
        row = result_row(config, repo, student, sha, code, error, client, sleep)
        rows.append(row)
        print(f"{repo}: " + (f"score {row['score']}" if row["score"] != "" else f"not graded ({row['error']})"))
        if code is not None and index + 1 < len(entries) and pause > 0:
            sleep(pause)
    write_csv(args.out, config, rows)
    graded = sum(1 for r in rows if r["score"] != "")
    print(f"Wrote {args.out}: {graded} graded, {len(rows) - graded} not graded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
