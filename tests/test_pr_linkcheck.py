"""Tests for the CI-only helpers in scripts/pr_linkcheck.py.

The one that matters is the ref pin: this script runs from the trusted base checkout on
pull_request_target, so the commit it reports on must be the commit the event announced —
a fork author can force-push pull/<N>/head between the event and the fetch. Stdlib only.
License: CC0.
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import pr_linkcheck  # noqa: E402


class HeadPinTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        self._cwd = os.getcwd()
        self._env = {k: os.environ.get(k) for k in ("PR_HEAD_SHA", "BASE_REF", "GITHUB_ACTIONS")}
        for k in self._env:
            os.environ.pop(k, None)
        run = lambda *a: subprocess.run(["git"] + list(a), cwd=self.repo, check=True,
                                        capture_output=True, text=True)
        run("init", "-q")
        run("config", "user.email", "t@example.com")
        run("config", "user.name", "T")
        with open(os.path.join(self.repo, "f.txt"), "w") as fh:
            fh.write("x")
        run("add", "f.txt")
        run("commit", "-q", "-m", "one")
        self.sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo,
                                  capture_output=True, text=True).stdout.strip()
        os.chdir(self.repo)

    def tearDown(self):
        os.chdir(self._cwd)
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    def test_accepts_the_announced_commit(self):
        os.environ["PR_HEAD_SHA"] = self.sha
        self.assertEqual(pr_linkcheck.assert_head_is_pinned("HEAD"), self.sha)

    def test_refuses_a_ref_that_moved_after_the_event(self):
        os.environ["PR_HEAD_SHA"] = "0" * 40
        with self.assertRaises(SystemExit) as caught:
            pr_linkcheck.assert_head_is_pinned("HEAD")
        self.assertIn("moved or mismatched", str(caught.exception))

    def test_requires_the_pin_in_ci(self):
        os.environ["GITHUB_ACTIONS"] = "true"
        with self.assertRaises(SystemExit):
            pr_linkcheck.assert_head_is_pinned("HEAD")

    def test_a_local_run_without_the_pin_is_allowed_but_warned(self):
        os.environ["BASE_REF"] = self.sha
        self.assertEqual(pr_linkcheck.assert_head_is_pinned("HEAD"), self.sha)


if __name__ == "__main__":
    unittest.main()
