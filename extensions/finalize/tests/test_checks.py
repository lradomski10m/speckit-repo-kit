"""Tests for scripts/checks.py with `gh` mocked. Run: python3 -m unittest discover extensions/finalize/tests"""

import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import checks  # noqa: E402

PROTECTION = "repos/o/r/branches/main/protection/required_status_checks"
UPGRADE = "gh: Upgrade to GitHub Pro or make this repository public to enable this feature. (HTTP 403)"
NOT_FOUND = "gh: Branch not protected (HTTP 404)"


def fake_gh(protection, runs):
    """A subprocess.run stand-in. protection: (returncode, stdout, stderr); runs: check-run list."""
    def run(cmd, **_):
        if cmd[0] == "git":
            return subprocess.CompletedProcess(cmd, 0, "feature\n", "")
        path = cmd[2]
        out = {
            "repos/o/r/pulls/7": {"html_url": "u", "head": {"sha": "abc"}, "state": "open",
                                  "mergeable": True, "base": {"ref": "main"}},
            "repos/o/r/commits/abc/check-runs?per_page=100": {"check_runs": runs},
            "repos/o/r/commits/abc/statuses?per_page=100": [],
        }
        if path == PROTECTION:
            code, stdout, stderr = protection
            return subprocess.CompletedProcess(cmd, code, stdout, stderr)
        return subprocess.CompletedProcess(cmd, 0, json.dumps(out[path]), "")
    return run


def run_of(name, conclusion, id=1):
    status = "completed" if conclusion else "in_progress"
    return {"id": id, "name": name, "status": status, "conclusion": conclusion, "started_at": "t"}


def main_with(protection, runs):
    argv = ["checks.py", "pr", "7", "--repo", "o/r", "--interval", "1", "--timeout", "1"]
    out, err = io.StringIO(), io.StringIO()
    with mock.patch("subprocess.run", fake_gh(protection, runs)), mock.patch.object(sys, "argv", argv), \
            mock.patch("time.sleep"), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = checks.main()
    return code, json.loads(out.getvalue()), err.getvalue()


class PrChecks(unittest.TestCase):
    def test_403_upgrade_means_no_required_checks(self):
        code, result, err = main_with((1, "", UPGRADE), [run_of("ci", "success")])
        self.assertEqual((code, result["state"]), (0, "green"))
        self.assertIn("no required checks known", err)

    def test_404_means_no_required_checks(self):
        code, result, _ = main_with((1, "", NOT_FOUND), [run_of("ci", "success")])
        self.assertEqual((code, result["state"]), (0, "green"))

    def test_failing_pr_check_still_exits_1_without_protection(self):
        code, result, _ = main_with((1, "", UPGRADE), [run_of("ci", "success"), run_of("lint", "failure", 2)])
        self.assertEqual((code, result["state"]), (1, "failed"))
        self.assertIn("lint", result["reason"])

    def test_pending_check_waits_then_times_out(self):
        code, result, _ = main_with((1, "", UPGRADE), [run_of("ci", None)])
        self.assertEqual((code, result["state"]), (2, "timeout"))

    def test_no_checks_at_all_is_not_green(self):
        code, result, _ = main_with((1, "", UPGRADE), [])
        self.assertNotEqual(code, 0)

    def test_normal_protection_requires_named_checks(self):
        body = json.dumps({"contexts": ["ci"], "checks": [{"context": "lint"}]})
        code, result, _ = main_with((0, body, ""), [run_of("ci", "success")])
        self.assertEqual(code, 2)  # lint is required but has not reported: wait
        self.assertIn("lint", result["reason"])
        code, result, _ = main_with((0, body, ""), [run_of("ci", "success"), run_of("lint", "success", 2)])
        self.assertEqual((code, result["state"]), (0, "green"))

    def test_other_errors_still_raise(self):
        with self.assertRaises(RuntimeError):
            main_with((1, "", "gh: Resource not accessible (HTTP 401)"), [run_of("ci", "success")])


if __name__ == "__main__":
    unittest.main()
