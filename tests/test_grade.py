"""Unit tests for the pure parts, with a fake client. Run: python -m unittest discover -s tests"""

import copy
import csv
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "autograding"))
sys.path.insert(0, str(ROOT / "teacher"))

import grade  # noqa: E402
import grade_all  # noqa: E402


class FakeError(Exception):
    def __init__(self, message, status):
        super().__init__(message)
        self.status = status


class FakeClient:
    """Returns the queued results (or raises the queued exceptions) and records the calls."""

    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def run_tests(self, compiler, code, tests, **options):
        self.calls.append({"compiler": compiler, "code": code, "tests": tests, "options": options})
        result = self.results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


CONFIG = {"title": "Shout", "compiler": "python-3.14", "file": "solution.py", "checker": "exact", "pass_mark": 70,
          "tests": [{"name": "a", "input": "x\n", "expected_output": "X\n", "weight": 2},
                    {"name": "secret name", "input": "y\n", "expected_output": "Y\n", "weight": 3, "hidden": True}]}


def result(score, verdicts, **extra):
    tests = [{"verdict": v, "hidden": i == 1, **({} if i == 1 else {"output": "x\n", "error": ""})}
             for i, v in enumerate(verdicts)]
    return {"verdict": "accepted" if score == 100 else "wrong_answer", "score": score, "passed": verdicts.count("accepted"),
            "total": len(verdicts), "tests": tests, "compile_error": None, "summary": "s", "submission": "sub_1", **extra}


class ConfigTests(unittest.TestCase):
    def test_defaults_are_filled(self):
        config = grade.validate_config({"compiler": "c", "file": "a.c", "tests": [{"expected_output": "1"}]})
        self.assertEqual(config["checker"], "trim")
        self.assertEqual(config["pass_mark"], 100)
        self.assertEqual(config["tests"][0]["name"], "test 1")
        self.assertEqual(config["tests"][0]["weight"], 1)

    def test_shipped_config_is_valid(self):
        config = grade.load_config(ROOT / "autograding" / "tests.json")
        self.assertTrue(any(t["hidden"] for t in config["tests"]))

    def test_rejects_bad_values(self):
        base = {"compiler": "c", "file": "a.c", "tests": [{"expected_output": "1"}]}
        for change in ({"compiler": ""}, {"file": 3}, {"checker": "magic"}, {"time_limit": 0}, {"time_limit": 31},
                       {"pass_mark": 101}, {"pass_mark": True}, {"tests": []}, {"tests": [1]},
                       {"tests": [{"input": "x"}]}, {"tests": [{"expected_output": "1", "weight": 0}]},
                       {"tests": [{"expected_output": "1", "hidden": "yes"}]}, {"tolerance": -1}):
            with self.assertRaises(grade.ConfigError, msg=str(change)):
                grade.validate_config({**base, **change})
        with self.assertRaises(grade.ConfigError):
            grade.validate_config([])

    def test_missing_and_invalid_json(self):
        with self.assertRaises(grade.ConfigError):
            grade.load_config("/nonexistent/tests.json")
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            handle.write("{nope")
        self.addCleanup(os.unlink, handle.name)
        with self.assertRaises(grade.ConfigError):
            grade.load_config(handle.name)


class GradeTests(unittest.TestCase):
    def setUp(self):
        self.config = grade.validate_config(copy.deepcopy(CONFIG))

    def test_request_options_and_pass(self):
        client = FakeClient(result(100, ["accepted", "accepted"]))
        outcome = grade.grade_code(self.config, "code", client)
        call = client.calls[0]
        self.assertEqual(call["options"], {"time_limit": 2, "checker": "exact", "scoring": "weighted"})
        self.assertEqual(call["tests"][1], {"input": "y\n", "expected_output": "Y\n", "weight": 3, "hidden": True})
        self.assertNotIn("name", call["tests"][0])
        self.assertTrue(outcome["passed"])

    def test_pass_mark_boundary(self):
        self.assertTrue(grade.build_grade(self.config, result(70, ["accepted", "wrong_answer"]))["passed"])
        self.assertFalse(grade.build_grade(self.config, result(69.9, ["accepted", "wrong_answer"]))["passed"])

    def test_hidden_tests_show_no_name_input_or_output(self):
        outcome = grade.build_grade(self.config, result(40, ["wrong_answer", "wrong_answer"]))
        hidden = outcome["tests"][1]
        self.assertEqual(hidden["name"], "Hidden test 1")
        self.assertNotIn("expected", hidden)
        text = grade.render_table(outcome) + grade.render_markdown(outcome)
        self.assertNotIn("secret name", text)
        self.assertNotIn("Y\\n", text)
        self.assertIn("expected 'X'", grade.render_table(outcome))

    def test_summary_markdown(self):
        outcome = grade.build_grade(self.config, result(40, ["wrong_answer", "accepted"]))
        markdown = grade.render_markdown(outcome)
        self.assertIn("Not passed", markdown)
        self.assertIn("| 1 | a | 2 | wrong answer |", markdown)
        self.assertIn("| 2 | Hidden test 1 | 3 | pass |", markdown)
        self.assertIn("Failed visible tests", markdown)

    def test_compile_error_is_shown(self):
        outcome = grade.build_grade(self.config, result(0, ["compile_error", "compile_error"],
                                                         compile_error="boom.c:1: error"))
        self.assertIn("boom.c:1: error", grade.render_table(outcome))
        self.assertIn("### Compile error", grade.render_markdown(outcome))

    def test_api_error_becomes_grade_error(self):
        with self.assertRaises(grade.GradeError) as caught:
            grade.grade_code(self.config, "c", FakeClient(FakeError("bad key", 401)))
        self.assertIn("bad key", str(caught.exception))

    def test_rate_limit_retried_once_only_when_asked(self):
        waits = []
        client = FakeClient(FakeError("slow down", 429), result(100, ["accepted", "accepted"]))
        outcome = grade.grade_code(self.config, "c", client, retry_rate_limit=True, wait=7, sleep=waits.append)
        self.assertEqual(waits, [7])
        self.assertTrue(outcome["passed"])
        with self.assertRaises(grade.GradeError):
            grade.grade_code(self.config, "c", FakeClient(FakeError("x", 429), FakeError("x", 429)),
                             retry_rate_limit=True, sleep=lambda s: None)
        with self.assertRaises(grade.GradeError):
            grade.grade_code(self.config, "c", FakeClient(FakeError("x", 429)))


class ErrorColumnTests(unittest.TestCase):
    def test_first_error_line_and_csv(self):
        config = grade.validate_config(copy.deepcopy(CONFIG))
        broken = {"verdict": "runtime_error", "score": 0, "passed": 0, "total": 2, "compile_error": None,
                  "tests": [{"verdict": "runtime_error", "output": "", "error": "Traceback\n  File x\nSyntaxError: bad\n"},
                            {"verdict": "runtime_error", "hidden": True, "error": "secret"}]}
        built = grade.build_grade(config, broken)
        self.assertEqual(built["first_error"], "SyntaxError: bad")
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "ann").mkdir()
            Path(folder, "ann", "solution.py").write_text("x")
            out = Path(folder, "g.csv")
            cfg = Path(folder, "t.json")
            cfg.write_text(json.dumps(CONFIG))
            grade_all.main(["--local", folder, "--config", str(cfg), "--out", str(out), "--pause", "0"],
                           client=FakeClient(broken))
            self.assertEqual(list(csv.DictReader(open(out)))[0]["error"], "SyntaxError: bad")


class MainTests(unittest.TestCase):
    def run_main(self, client, code, **env):
        with tempfile.TemporaryDirectory() as folder:
            student = Path(folder, "solution.py")
            student.write_text(code)
            config = Path(folder, "tests.json")
            config.write_text(json.dumps(CONFIG))
            summary = Path(folder, "summary.md")
            old = os.environ.get("GITHUB_STEP_SUMMARY")
            os.environ["GITHUB_STEP_SUMMARY"] = str(summary)
            try:
                status = grade.main(["--config", str(config), "--file", str(student)], client=client)
            finally:
                if old is None:
                    del os.environ["GITHUB_STEP_SUMMARY"]
                else:
                    os.environ["GITHUB_STEP_SUMMARY"] = old
            return status, summary.read_text() if summary.exists() else ""

    def test_exit_codes(self):
        status, text = self.run_main(FakeClient(result(100, ["accepted", "accepted"])), "print(1)")
        self.assertEqual(status, 0)
        self.assertIn("Passed", text)
        status, _ = self.run_main(FakeClient(result(0, ["wrong_answer", "wrong_answer"])), "print(1)")
        self.assertEqual(status, 1)
        status, text = self.run_main(FakeClient(FakeError("nope", 500)), "print(1)")
        self.assertEqual(status, 2)
        self.assertIn("Could not grade", text)

    def test_missing_student_file(self):
        self.assertEqual(grade.main(["--config", str(ROOT / "autograding" / "tests.json"), "--file", "/nope.py"],
                                    client=FakeClient()), 2)


class GradeAllTests(unittest.TestCase):
    def test_student_of(self):
        self.assertEqual(grade_all.student_of("hw1-ada", "hw1-"), "ada")
        self.assertEqual(grade_all.student_of("hw1", "hw1"), "hw1")

    def test_safe_cell(self):
        self.assertEqual(grade_all.safe_cell("=1+1"), "'=1+1")
        self.assertEqual(grade_all.safe_cell("ada"), "ada")

    def test_list_repos_filters_and_sorts(self):
        out = json.dumps([{"name": "hw1-b"}, {"name": "hw2-a"}, {"name": "hw1-a"}])
        calls = []

        def run(*args):
            calls.append(args)
            return out
        repos = grade_all.list_repos("org", "hw1-", run)
        self.assertEqual([r["name"] for r in repos], ["hw1-a", "hw1-b"])
        self.assertIn("--limit", calls[0])

    def test_local_mode_writes_csv(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ("ada", "bob", "eve"):
                Path(folder, name).mkdir()
            Path(folder, "ada", "solution.py").write_text("ok")
            Path(folder, "bob", "solution.py").write_text("bad")
            out = Path(folder, "grades.csv")
            client = FakeClient(result(100, ["accepted", "accepted"]), result(0, ["wrong_answer", "wrong_answer"]))
            config = Path(folder, "tests.json")
            config.write_text(json.dumps(CONFIG))
            status = grade_all.main(["--local", folder, "--config", str(config), "--out", str(out), "--pause", "0"],
                                    client=client)
            self.assertEqual(status, 0)
            rows = list(csv.DictReader(open(out)))
            self.assertEqual([r["repo"] for r in rows], ["ada", "bob", "eve"])
            self.assertEqual([r["score"] for r in rows], ["100", "0", ""])
            self.assertEqual(rows[0]["Hidden test 1"], "accepted")
            self.assertIn("not found", rows[2]["error"])
            self.assertEqual(len(client.calls), 2)


if __name__ == "__main__":
    unittest.main()
