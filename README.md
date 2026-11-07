# Classroom autograder

An assignment template that grades student code on every push, without GitHub Classroom. It runs the student's program against your test cases in a sandbox through the [OnlineCompiler.io](https://onlinecompiler.io/?utm_source=github&utm_medium=repo&utm_campaign=classroom-autograder) code execution API and puts the score in the GitHub Actions job summary.

**Who it is for:** teachers who used GitHub Classroom (deprecated on 28 August 2026) and still have a GitHub organization with assignment repositories. You keep GitHub for the repos and replace the autograding step. For the full story of how Classroom's input/output tests map to the API, read the [guide](https://onlinecompiler.io/guides/github-classroom-autograding-alternative?utm_source=github&utm_medium=repo&utm_campaign=classroom-autograder).

This repository is not a roster, gradebook or invitation system. It does one job: run code against tests and report a score.

## How it works

```
student pushes solution.py
        |
        v
.github/workflows/autograde.yml   (GitHub Actions, secret ONLINECOMPILER_API_KEY)
        |
        v
autograding/grade.py  --- reads autograding/tests.json, sends the code and the tests to run-tests
        |
        v
job summary with a score and a result per test; the check fails below the pass mark
```

| File | What it is |
|---|---|
| `solution.py` | The student's file (a starter with a TODO). |
| `autograding/tests.json` | The assignment: compiler, student file, checker, time limit, pass mark, tests. |
| `autograding/grade.py` | Grades one file. Also runs locally. |
| `.github/workflows/autograde.yml` | Runs `grade.py` on push and manual dispatch. Skipped in the template repository itself. |
| `teacher/grade_all.py` | Grades every student repo of an assignment into `grades.csv`. |

The example assignment: read one line, print it in capital letters.

## Setup for a teacher

1. **Create a free API key** at <https://api.onlinecompiler.io/compilers/api-keys/?utm_source=github&utm_medium=repo&utm_campaign=classroom-autograder>. Make a key just for this class (see [Limits and caveats](#limits-and-caveats)).
2. **Copy this template** into your organization: click "Use this template" on GitHub and name the new repository after the assignment, for example `hw1-template`.
3. **Mark it as a template first** (repository Settings, tick "Template repository"), before you push anything to it. The workflow skips template repositories, so your edits to the template never run a grade.
4. **Edit the assignment** in the template: put your tests in `autograding/tests.json` and your starter code in the student file (see below).
5. **Make the key available to the student repos** (read "Where the secret can live" just below).
6. **Create each student's repository from the template**, named with a common prefix and the student's login, for example `hw1-ada`, `hw1-bob`, and give the student write access. Each new repository runs the workflow once on the starter code when it is created, so the first run shows a score of 0 and a red check. Tell students to expect it.
7. **Check it worked:** push to one student repo and open the Actions tab. The run's summary shows the score.

#### Where the secret can live

The workflow reads a secret named `ONLINECOMPILER_API_KEY`. Student repositories are usually private, and on a **GitHub Free organization, organization secrets are not available to private repositories**. Pick one:

- **Organization secret, on a paid plan** (GitHub Team, or the free GitHub Education benefits for teachers if your school has them). Organization settings, Secrets and variables, Actions, New organization secret. Under "Repository access" choose **Private repositories**, so every new student repo gets the key without you selecting it again. The template repository receives the secret too, which is harmless because its workflow is skipped.
- **A repository secret per student repo**, on any plan, scripted with the GitHub CLI after you create the repos:

  ```bash
  export ONLINECOMPILER_API_KEY=...        # the class key
  for repo in $(gh repo list MY-ORG --json name --limit 1000 --jq '.[].name | select(startswith("hw1-"))'); do
    gh secret set ONLINECOMPILER_API_KEY --repo "MY-ORG/$repo" --body "$ONLINECOMPILER_API_KEY"
  done
  ```

  Run it again whenever you add repos.
- **Public student repositories**, which can read organization secrets on any plan. The cost: anyone can read the students' code, your tests and your workflow.

### The assignment file: `autograding/tests.json`

```json
{
  "title": "Shout",
  "compiler": "python-3.14",
  "file": "solution.py",
  "checker": "trim",
  "time_limit": 2,
  "pass_mark": 70,
  "tests": [
    {"name": "lowercase word", "input": "hello\n", "expected_output": "HELLO\n", "weight": 2},
    {"name": "secret case", "input": "abc 123 !?\n", "expected_output": "ABC 123 !?\n", "weight": 3, "hidden": true}
  ]
}
```

| Field | Meaning |
|---|---|
| `compiler` | The language id, for example `python-3.14`. |
| `file` | The student's file in the repo, sent as the program. |
| `checker` | How output is compared: `trim` (ignores trailing whitespace, the default), `exact`, `tokens` (ignores all whitespace differences), `float` (numbers within `tolerance`) or `rows` (lines as an unordered set). |
| `time_limit` | Seconds per test, above 0 and up to 30 (default 2). |
| `pass_mark` | The score (0 to 100) needed for the check to pass (default 100). |
| `tolerance` | Optional, for the `float` checker. |
| `tests[].input` | Standard input for the program. |
| `tests[].expected_output` | What it should print. |
| `tests[].weight` | How much the test counts. The score is the share of the total weight that passed, from 0 to 100. |
| `tests[].hidden` | If true, the job summary shows only whether it passed, never its input or output. Read the caveats before relying on this. |
| `tests[].name` | Shown in the summary. Hidden tests are shown as "Hidden test N" instead. |

The number of tests is capped by your plan's pending limit: 8 on Free, 20 on Starter, 40 on Pro, 80 on Business; the API refuses a longer list and `grade.py` shows its message. The sandbox gives each test a fresh container with no network.

### Another language

Change `compiler` and `file` in `tests.json`, and replace `solution.py` with a starter in that language. The workflow and scripts stay the same. Ids come from the [compiler list](https://onlinecompiler.io/languages?utm_source=github&utm_medium=repo&utm_campaign=classroom-autograder); all 46 languages work.

**C++** (`g++-15`):

```json
"compiler": "g++-15",
"file": "solution.cpp",
```

```cpp
#include <iostream>
#include <string>

int main() {
    std::string line;
    std::getline(std::cin, line);
    // TODO: print the line in capital letters
}
```

**Java** (`openjdk-25`): use a public class `Main` with `public static void main(String[] args)`, in a file called `Main.java`, as below. The service names the file after the class that declares `main`; this layout was checked against the API with the example assignment.

```json
"compiler": "openjdk-25",
"file": "Main.java",
```

```java
import java.util.Scanner;

public class Main {
    public static void main(String[] args) {
        Scanner in = new Scanner(System.in);
        String line = in.nextLine();
        // TODO: print the line in capital letters
    }
}
```

Delete `solution.py` when you switch, so students do not see a stray file. Several source files per student are possible through the API's `files` option, but this template sends the one file named in `file`.

## What the student sees

Each push starts the **Autograde** workflow. On the run's page the job summary shows:

- the score out of 100 and whether it reached the pass mark;
- a table with one row per test: pass, or the verdict (wrong answer, runtime error, time limit exceeded, compile error);
- for a failed visible test, what was expected and what the program printed;
- a compile error when there is one;
- for hidden tests, only pass or fail.

The check on the commit is green at or above the pass mark and red below it. Students can also run the grader on their own machine: `pip install -r autograding/requirements.txt`, then `python autograding/grade.py` with `ONLINECOMPILER_API_KEY` set. That spends your key, so most teachers will leave it to Actions.

If the secret is missing, the job fails at once with a message saying so.

## Grading a whole class

`teacher/grade_all.py` lists the repositories of an organization whose names start with a prefix, takes each one's student file at the head of the default branch, grades it with the same code the workflow uses, and writes `grades.csv`.

```bash
gh auth login                                  # once; needs read access to the student repos
export ONLINECOMPILER_API_KEY=...              # the class key
pip install -r autograding/requirements.txt
python teacher/grade_all.py my-org hw1-
```

It reads `autograding/tests.json` from the checkout you run it in, so run it from the teacher's copy of the assignment with the same settings the students have. The CSV has one row per repository: repo, student (the repo name after the prefix), commit sha, score, passed, one column of verdicts per test, and an error column (for example when the student file is missing). Repositories are graded one after another, paced under the free plan's per-minute limit, and a 429 is waited out and retried once.

Already exported the repositories? Put them in one folder, one subfolder per student, with the student file at the same path as in the repo, and run:

```bash
python teacher/grade_all.py --local ./submissions
```

Add `--out file.csv` to name the output, `--pause 0` to skip the pacing on a plan with a higher rate limit.

## Limits and caveats

- **Visible tests are readable.** Everything in `autograding/tests.json` is in the student's repository, so a student can read every input and expected output, hidden ones included. `hidden` only keeps them out of the job summary. A student can hard-code the answers. If that matters, do not put the real tests in the repo.
- **For hidden tests, use stored problems.** A stored problem keeps its tests on the server and returns only verdicts; the student's code is sent to it by id. The key then stays on a server of yours, never in the student repos. The [guide](https://onlinecompiler.io/guides/github-classroom-autograding-alternative?utm_source=github&utm_medium=repo&utm_campaign=classroom-autograder) shows how.
- **Students with write access can change the workflow.** The secret is available to the workflow in their repository, and a student who edits `autograde.yml` or `grade.py` can print the key, use it for other things, or make the check pass. Use a key made only for this class, with its own limits, watch its use in the dashboard, and disable it after the term. Never use your main key. Branch protection and required workflow rules in your organization can limit who changes the workflow; they do not make it tamper-proof.
- **Grading runs on push only**, in the student's own repository. Pull requests are not graded, which also keeps forks (which never receive secrets) out of it.
- **Every test counts as one run** of your monthly allowance. The free plan includes 100,000 runs a month. An assignment with 8 tests graded on each of 5 pushes by 100 students is 4,000 runs. The Free plan also limits pending runs to 8 and submissions to 30 a minute, and a request with 8 tests uses all 8 pending slots. Two students pushing at the same moment, or a class pushing at the deadline, can therefore get a 429; the job then fails with "Could not grade: RateLimitError ..." (exit code 2). Re-run the workflow a minute later. A paid plan raises these limits.
- **Grades come from the last run.** The check reflects the commit it ran on. For final grades, use `teacher/grade_all.py` at your deadline, which grades the head of each default branch.
- **One file per student, standard input and output.** Tests that need data files, several source files or command-line arguments need the API options directly; see the [docs](https://onlinecompiler.io/docs?utm_source=github&utm_medium=repo&utm_campaign=classroom-autograder).

## Tests

```bash
python -m unittest discover -s tests
```

These use a fake client and make no network calls or API runs.

## License

MIT, 2026, RunCode Inc. See [LICENSE](LICENSE).
