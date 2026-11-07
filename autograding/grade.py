"""Grade a student's file against autograding/tests.json with OnlineCompiler.io run-tests.

    python autograding/grade.py [--config autograding/tests.json] [--file solution.py]

Reads ONLINECOMPILER_API_KEY from the environment. Prints a table of the tests, writes a Markdown
summary to $GITHUB_STEP_SUMMARY when that is set, and exits with:

    0  the score reached pass_mark
    1  the score is below pass_mark
    2  nothing was graded (bad config, missing file or key, API error)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable

KEY_URL = ("https://api.onlinecompiler.io/compilers/api-keys/"
           "?utm_source=github&utm_medium=repo&utm_campaign=classroom-autograder")
CHECKERS = ("trim", "exact", "tokens", "float", "rows")
SNIPPET = 200
DEFAULT_CONFIG = Path(__file__).resolve().parent / "tests.json"


class ConfigError(Exception):
    """tests.json is missing, unreadable or invalid."""


class GradeError(Exception):
    """The submission could not be graded (not a wrong answer: nothing was judged)."""


def load_config(path: str | os.PathLike) -> dict[str, Any]:
    try:
        with open(path, encoding="utf-8") as handle:
            config = json.load(handle)
    except FileNotFoundError:
        raise ConfigError(f"{path} not found") from None
    except ValueError as error:
        raise ConfigError(f"{path} is not valid JSON: {error}") from None
    return validate_config(config)


def validate_config(config: Any) -> dict[str, Any]:
    """Check the shape of tests.json and fill in the defaults. Returns a new dict."""
    if not isinstance(config, dict):
        raise ConfigError("tests.json must be a JSON object")
    for key in ("compiler", "file"):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise ConfigError(f'"{key}" must be a non-empty string')
    checker = config.get("checker", "trim")
    if checker not in CHECKERS:
        raise ConfigError(f'"checker" must be one of {", ".join(CHECKERS)}')
    time_limit = config.get("time_limit", 2)
    if isinstance(time_limit, bool) or not isinstance(time_limit, (int, float)) or not 0 < time_limit <= 30:
        raise ConfigError('"time_limit" must be a number above 0 and up to 30 (seconds per test)')
    pass_mark = config.get("pass_mark", 100)
    if isinstance(pass_mark, bool) or not isinstance(pass_mark, (int, float)) or not 0 <= pass_mark <= 100:
        raise ConfigError('"pass_mark" must be a number from 0 to 100')
    tolerance = config.get("tolerance")
    if tolerance is not None and (isinstance(tolerance, bool) or not isinstance(tolerance, (int, float))
                                  or tolerance < 0):
        raise ConfigError('"tolerance" must be a number of 0 or more')
    tests = config.get("tests")
    if not isinstance(tests, list) or not tests:
        raise ConfigError('"tests" must be a non-empty list')
    cleaned = []
    for number, test in enumerate(tests, 1):
        if not isinstance(test, dict):
            raise ConfigError(f"test {number} must be an object")
        if not isinstance(test.get("expected_output"), str):
            raise ConfigError(f'test {number}: "expected_output" must be a string')
        if not isinstance(test.get("input", ""), str):
            raise ConfigError(f'test {number}: "input" must be a string')
        if not isinstance(test.get("hidden", False), bool):
            raise ConfigError(f'test {number}: "hidden" must be true or false')
        weight = test.get("weight", 1)
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or weight <= 0:
            raise ConfigError(f'test {number}: "weight" must be a number above 0')
        name = test.get("name", f"test {number}")
        if not isinstance(name, str) or not name.strip():
            raise ConfigError(f'test {number}: "name" must be a non-empty string')
        cleaned.append({"name": name, "input": test.get("input", ""), "expected_output": test["expected_output"],
                        "weight": weight, "hidden": bool(test.get("hidden", False))})
    result = {"title": str(config.get("title") or "Assignment"), "compiler": config["compiler"].strip(),
              "file": config["file"].strip(), "checker": checker, "time_limit": time_limit,
              "pass_mark": pass_mark, "tests": cleaned}
    if tolerance is not None:
        result["tolerance"] = tolerance
    return result


def api_tests(config: dict[str, Any]) -> list[dict[str, Any]]:
    """The tests as the API takes them (the names stay here)."""
    return [{"input": t["input"], "expected_output": t["expected_output"], "weight": t["weight"],
             **({"hidden": True} if t["hidden"] else {})} for t in config["tests"]]


def make_client():
    try:
        from onlinecompiler import OnlineCompiler
    except ImportError:
        raise GradeError("The onlinecompiler package is missing: pip install onlinecompiler") from None
    if not os.environ.get("ONLINECOMPILER_API_KEY"):
        raise GradeError("ONLINECOMPILER_API_KEY is not set. Create a free key at "
                         f"{KEY_URL} and add it as a repository or organization secret named "
                         "ONLINECOMPILER_API_KEY (locally: export it).")
    return OnlineCompiler()


def grade_code(config: dict[str, Any], code: str, client: Any, *, retry_rate_limit: bool = False,
               wait: float = 60, sleep: Callable[[float], None] = time.sleep) -> dict[str, Any]:
    """Judge code and return the grade. Raises GradeError when nothing was judged.

    retry_rate_limit waits `wait` seconds and tries once more after a 429 (used when grading a whole class)."""
    options: dict[str, Any] = {"time_limit": config["time_limit"], "checker": config["checker"],
                               "scoring": "weighted"}
    if "tolerance" in config:
        options["tolerance"] = config["tolerance"]
    attempts = 2 if retry_rate_limit else 1
    for attempt in range(attempts):
        try:
            result = client.run_tests(config["compiler"], code, api_tests(config), **options)
            break
        except Exception as error:  # the client's OnlineCompilerError and its subclasses, or a network error
            status = getattr(error, "status", None)
            if status == 429 and attempt + 1 < attempts:
                sleep(wait)
                continue
            raise GradeError(f"{type(error).__name__}: {error}") from None
    return build_grade(config, result)


def build_grade(config: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    score = float(result.get("score") or 0)
    rows = []
    hidden_seen = 0
    for number, test in enumerate(config["tests"], 1):
        returned = (result.get("tests") or [])[number - 1] if number <= len(result.get("tests") or []) else {}
        hidden = test["hidden"]
        if hidden:
            hidden_seen += 1
        row = {"number": number, "name": f"Hidden test {hidden_seen}" if hidden else test["name"],
               "verdict": returned.get("verdict", "not_run"), "hidden": hidden, "weight": test["weight"]}
        if not hidden and row["verdict"] != "accepted":
            row["expected"] = test["expected_output"]
            row["output"] = returned.get("output", "")
            row["error"] = returned.get("error", "")
        rows.append(row)
    first_error = ""
    if result.get("compile_error"):
        first_error = _first_line(result["compile_error"])
    for row, test in zip(rows, result.get("tests") or []):
        if not first_error and not row["hidden"] and test.get("error"):
            first_error = _first_line(test["error"])
    return {"title": config["title"], "first_error": first_error, "verdict": result.get("verdict", "unknown"), "score": score,
            "pass_mark": config["pass_mark"], "passed": score >= config["pass_mark"],
            "tests_passed": result.get("passed", 0), "tests_total": result.get("total", len(rows)),
            "compile_error": result.get("compile_error"), "summary": result.get("summary", ""),
            "submission": result.get("submission"), "tests": rows}


def _first_line(text: str) -> str:
    """The last non-empty line of stderr (where Python and Java put the error), clipped."""
    lines = [line.strip() for line in text.replace("\r", "").split("\n") if line.strip()]
    return _clip(lines[-1]) if lines else ""


def _clip(text: str) -> str:
    text = text if len(text) <= SNIPPET else text[:SNIPPET] + "..."
    return text.replace("\r", "").rstrip("\n")


def _score(value: float) -> str:
    return f"{value:g}" if value == int(value) else f"{value:.1f}"


def render_table(grade: dict[str, Any]) -> str:
    width = max([len(r["name"]) for r in grade["tests"]] + [4])
    lines = [f"{grade['title']}: {grade['verdict']}", "",
             f"{'#':>3}  {'Test':<{width}}  {'Weight':>6}  Verdict"]
    for row in grade["tests"]:
        lines.append(f"{row['number']:>3}  {row['name']:<{width}}  {row['weight']:>6g}  {row['verdict']}")
    lines += ["", f"Score {_score(grade['score'])}/100, pass mark {_score(grade['pass_mark'])}: "
                  + ("PASSED" if grade["passed"] else "NOT PASSED")]
    if grade["compile_error"]:
        lines += ["", "Compile error:", grade["compile_error"].rstrip()]
    for row in grade["tests"]:
        if row.get("expected") is not None:
            lines += ["", f"{row['name']}: expected {_clip(row['expected'])!r}, got {_clip(row['output'])!r}"]
            if row["error"]:
                lines.append(f"  stderr: {_clip(row['error'])}")
    return "\n".join(lines)


def _cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ")


def render_markdown(grade: dict[str, Any]) -> str:
    status = "Passed" if grade["passed"] else "Not passed"
    lines = [f"## {_cell(grade['title'])}: {status}", "",
             f"**Score {_score(grade['score'])}/100** (pass mark {_score(grade['pass_mark'])}). "
             f"{grade['tests_passed']} of {grade['tests_total']} tests passed.", "",
             "| # | Test | Weight | Result |", "|---|------|-------:|--------|"]
    for row in grade["tests"]:
        mark = "pass" if row["verdict"] == "accepted" else row["verdict"].replace("_", " ")
        lines.append(f"| {row['number']} | {_cell(row['name'])} | {row['weight']:g} | {mark} |")
    if grade["compile_error"]:
        lines += ["", "### Compile error", "", "```", grade["compile_error"].rstrip(), "```"]
    failed = [r for r in grade["tests"] if r.get("expected") is not None]
    if failed:
        lines += ["", "### Failed visible tests"]
        for row in failed:
            lines += ["", f"**{_cell(row['name'])}**", "", "```",
                      f"expected: {_clip(row['expected'])}", f"got:      {_clip(row['output'])}"]
            if row["error"]:
                lines.append(f"stderr:   {_clip(row['error'])}")
            lines.append("```")
    if any(r["hidden"] for r in grade["tests"]):
        lines += ["", "Hidden tests show only whether they passed."]
    return "\n".join(lines) + "\n"


def read_code(path: str | os.PathLike) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        raise GradeError(f"The student file {path} was not found") from None
    except (UnicodeDecodeError, OSError) as error:
        raise GradeError(f"The student file {path} could not be read: {error}") from None


def main(argv: list[str] | None = None, client: Any = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--config", default=str(DEFAULT_CONFIG), help="path to tests.json")
    parser.add_argument("--file", help='student file (default: "file" in tests.json, relative to the repo root)')
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        code = read_code(args.file or config["file"])
        grade = grade_code(config, code, client or make_client())
    except (ConfigError, GradeError) as error:
        print(f"Could not grade: {error}", file=sys.stderr)
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as handle:
                handle.write(f"## Could not grade\n\n{error}\n")
        return 2
    print(render_table(grade))
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(render_markdown(grade))
    return 0 if grade["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
