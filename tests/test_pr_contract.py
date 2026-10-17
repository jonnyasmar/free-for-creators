"""Tests for the PR submission contract (scripts/check_pr.py).

The bodies used here are built from the real .github/PULL_REQUEST_TEMPLATE.md rather than
hand-written, and one test asserts that every confirmation the gate requires is still a
literal checkbox in that template. That pair is the point: the gate is allowed to be
literal-minded as long as the thing it reads is the thing contributors are given. Edit the
template wording without editing REQUIRED_CONFIRMATIONS and these tests fail, instead of
the gate silently rejecting every contributor.

Stdlib only. License: CC0.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_pr  # noqa: E402

TEMPLATE = (ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text(encoding="utf-8")

ENTRY_A = {
    "name": "Example One",
    "url": "https://one.example/",
    "description": "One free clip library.",
    "description_zh": "一个免费素材库。",
    "license": "CC0",
    "license_family": "cc0",
    "attribution": "not-required",
    "monetization": "allowed",
    "signup": "not-required",
}
ENTRY_B = dict(ENTRY_A, name="Example Two", url="https://two.example/",
               description="Two free clip library.", description_zh="另一个免费素材库。")

CATEGORY = {
    "category": "Example",
    "category_zh": "示例",
    "slug": "example",
    "description": "Example category.",
    "description_zh": "示例分类。",
}


def body_from_template(name="Example Two", url="https://two.example/",
                       ownership="No", tick=True):
    text = TEMPLATE
    if tick:
        text = text.replace("- [ ]", "- [x]")
    text = text.replace("Resource name:", "Resource name: %s" % name)
    text = text.replace("Link: https://", "Link: %s" % url)
    text = text.replace("Is this your product? Yes / No", "Is this your product? %s" % ownership)
    return text


def pr_dict(title, body, login="contributor", association="NONE", head="", base=""):
    return {
        "title": title, "body": body, "user": {"login": login},
        "author_association": association,
        "head": {"sha": head}, "base": {"sha": base},
    }


class RepoFixture(unittest.TestCase):
    """A throwaway git repo whose data/ the contract checker reads through git objects."""

    def setUp(self):
        self._cwd = os.getcwd()
        self._report = os.environ.pop("PR_REPORT", None)
        self._pin = os.environ.pop("PR_HEAD_SHA", None)
        self._tmp = tempfile.TemporaryDirectory()
        os.chdir(self._tmp.name)
        self.git("init", "-q")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "T")
        Path("data").mkdir()
        Path("README.md").write_text("readme\n", encoding="utf-8")
        self._write_category([ENTRY_A])
        self.commit("base")
        self.base = self.rev()

    def tearDown(self):
        os.chdir(self._cwd)
        if self._report is not None:
            os.environ["PR_REPORT"] = self._report
        if self._pin is not None:
            os.environ["PR_HEAD_SHA"] = self._pin
        self._tmp.cleanup()

    def git(self, *args):
        return subprocess.run(["git"] + list(args), capture_output=True, text=True, check=True).stdout

    def rev(self):
        return self.git("rev-parse", "HEAD").strip()

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.rev()

    def _write_category(self, entries):
        doc = dict(CATEGORY, entries=entries)
        Path("data", "example.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    def add_entry(self, entry, touch_readme=True):
        self._write_category([e for e in (ENTRY_A, entry) if e])
        if touch_readme:
            Path("README.md").write_text("readme + %s\n" % entry["name"], encoding="utf-8")
        return self.commit("add %s" % entry["name"])

    def replace_entries(self, entries):
        self._write_category(entries)
        return self.commit("edit data")

    def check(self, title, body, base=None, head=None):
        base = base or self.base
        head = head or self.rev()
        return check_pr.validate(base, head, pr_dict(title, body, head=head, base=base),
                                 write_report=False)


class AcceptanceTests(RepoFixture):
    def test_accepts_one_added_entry(self):
        self.add_entry(ENTRY_B)
        errors, warnings = self.check("[Example Two] -> [Example]",
                                      body_from_template("Example Two", "https://two.example/"))
        self.assertEqual(errors, [])
        self.assertEqual(warnings, [])

    def test_accepts_a_correction_that_keeps_the_name(self):
        fixed = dict(ENTRY_A, url="https://one-new.example/")
        self.replace_entries([fixed])
        errors, _ = self.check("[Example One] -> [Example]",
                               body_from_template("Example One", "https://one-new.example/"))
        self.assertEqual(errors, [])

    def test_accepts_a_correction_that_keeps_only_the_url(self):
        renamed = dict(ENTRY_A, name="Example One Reborn")
        self.replace_entries([renamed])
        errors, _ = self.check("[Example One Reborn] -> [Example]",
                               body_from_template("Example One Reborn", "https://one.example/"))
        self.assertEqual(errors, [])

    def test_accepts_the_removal_of_an_entry(self):
        self.add_entry(ENTRY_B)
        base = self.rev()  # the base a remover branches from already lists the entry
        self.replace_entries([ENTRY_A])
        errors, _ = self.check("[Example Two] -> [Example]",
                               body_from_template("Example Two", "https://two.example/"),
                               base=base, head=self.rev())
        self.assertEqual(errors, [])


class SingleChangeTests(RepoFixture):
    def test_rejects_two_entries_in_one_pr(self):
        self._write_category([ENTRY_A, ENTRY_B, dict(ENTRY_A, name="Example Three",
                                                     url="https://three.example/")])
        head = self.commit("three entries")
        errors, _ = self.check("[Example Two] -> [Example]",
                               body_from_template(), head=head)
        self.assertTrue(any("changes exactly one entry" in e for e in errors), errors)

    def test_rejects_a_pr_that_changes_no_entry(self):
        doc = dict(CATEGORY, description="Reworded category.", entries=[ENTRY_A])
        Path("data", "example.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        head = self.commit("reword category")
        errors, _ = self.check("[Example] -> [Example]", body_from_template(), head=head)
        self.assertTrue(any("does not add, correct or remove an entry" in e for e in errors), errors)

    def test_rejects_a_new_category_file(self):
        Path("data", "second.json").write_text(
            json.dumps(dict(CATEGORY, slug="second", category="Second", entries=[ENTRY_B])),
            encoding="utf-8")
        head = self.commit("new category")
        errors, _ = self.check("[Example Two] -> [Second]", body_from_template(), head=head)
        self.assertTrue(any("category" in e and "maintainer" in e for e in errors), errors)

    def test_rejects_files_outside_the_submission_allowlist(self):
        self.add_entry(ENTRY_B)
        Path("scripts").mkdir(exist_ok=True)
        Path("scripts", "build_readme.py").write_text("# edited\n", encoding="utf-8")
        head = self.commit("also edit a script")
        errors, _ = self.check("[Example Two] -> [Example]", body_from_template(), head=head)
        self.assertTrue(any("outside the file list" in e for e in errors), errors)


class ProposalTests(RepoFixture):
    def setUp(self):
        super().setUp()
        self.head = self.add_entry(ENTRY_B)

    def check(self, title, body):
        return super().check(title, body, head=self.head)

    def test_rejects_a_title_without_the_pair_format(self):
        errors, _ = self.check("Add Example Two", body_from_template())
        self.assertTrue(any("[Resource name] -> [Category]" in e for e in errors), errors)

    def test_rejects_a_title_that_names_another_resource(self):
        errors, _ = self.check("[Example Nine] -> [Example]", body_from_template())
        self.assertTrue(any("must match" in e for e in errors), errors)

    def test_rejects_a_title_with_the_wrong_category(self):
        errors, _ = self.check("[Example Two] -> [Fonts]", body_from_template())
        self.assertTrue(any("category" in e for e in errors), errors)

    def test_rejects_a_body_without_a_link_line(self):
        body = body_from_template().replace("Link: https://two.example/", "Link: see website")
        errors, _ = self.check("[Example Two] -> [Example]", body)
        self.assertTrue(any("Link:" in e for e in errors), errors)

    def test_rejects_a_link_that_is_not_the_entry_url(self):
        errors, _ = self.check("[Example Two] -> [Example]",
                               body_from_template("Example Two", "https://elsewhere.example/"))
        self.assertTrue(any("not the entry's URL" in e for e in errors), errors)

    def test_rejects_the_untouched_ownership_placeholder(self):
        errors, _ = self.check("[Example Two] -> [Example]", TEMPLATE)
        self.assertTrue(any("Is this your product" in e for e in errors), errors)

    def test_rejects_an_unanswered_ownership_line(self):
        body = body_from_template().replace("Is this your product? No", "Is this your product?")
        errors, _ = self.check("[Example Two] -> [Example]", body)
        self.assertTrue(any("Is this your product" in e for e in errors), errors)

    def test_accepts_a_disclosed_self_submission(self):
        errors, _ = self.check("[Example Two] -> [Example]", body_from_template(ownership="Yes"))
        self.assertEqual(errors, [])

    def test_rejects_an_unticked_checklist_item(self):
        errors, _ = self.check("[Example Two] -> [Example]", body_from_template(tick=False))
        self.assertTrue(any("was not ticked" in e for e in errors), errors)

    def test_rejects_a_reworded_checklist_item(self):
        body = body_from_template().replace(
            "I ran `python3 scripts/build_readme.py` and committed the regenerated README",
            "I regenerated the readme")
        errors, _ = self.check("[Example Two] -> [Example]", body)
        self.assertTrue(any("was not ticked" in e for e in errors), errors)

    def test_reports_which_box_is_missing(self):
        body = body_from_template().replace(
            "The free tier is not a trial, an expiring credit, a recurring quota or a watermarked export",
            "x")
        errors, _ = self.check("[Example Two] -> [Example]", body)
        self.assertTrue(any("expiring credit" in e for e in errors), errors)


class TemplateConsistencyTests(unittest.TestCase):
    def test_every_required_confirmation_is_a_checkbox_in_the_shipped_template(self):
        items = [check_pr.normalize_item(text) for _, text in check_pr.CHECKED_ITEM_PATTERN.findall(TEMPLATE)]
        for fragment, _ in check_pr.REQUIRED_CONFIRMATIONS:
            self.assertTrue(any(fragment in item for item in items),
                            "the gate requires a checkbox containing %r, but the shipped "
                            "template has no such line" % fragment)

    def test_the_template_asks_for_the_title_format(self):
        self.assertIn("[Resource name] -> [Category]", TEMPLATE)

    def test_the_template_asks_for_a_link_and_a_disclosure(self):
        self.assertIn("Link: https://", TEMPLATE)
        self.assertIn("Is this your product?", TEMPLATE)


class ExemptionTests(RepoFixture):
    def run_main(self, association, login, argv_extra=None):
        event = Path("event.json")
        event.write_text(json.dumps({"pull_request": pr_dict(
            "Add a batch of entries", body_from_template(), login=login,
            association=association, head=self.base, base=self.base)}), encoding="utf-8")
        argv = ["check_pr.py", "--event", str(event), "--base-ref", self.base,
                "--head-ref", self.base]
        with mock.patch.object(sys, "argv", argv + (argv_extra or [])):
            return check_pr.main()

    def test_a_maintainer_is_exempt(self):
        self.assertEqual(self.run_main("OWNER", "skyzhao1223"), 0)

    def test_a_bot_is_exempt(self):
        self.assertEqual(self.run_main("NONE", "dependabot[bot]"), 0)

    def test_a_contributor_is_not_exempt(self):
        self.assertEqual(self.run_main("NONE", "someone", ["--force"]), 1)

    def test_a_head_ref_that_moved_is_refused(self):
        # The event says one commit; pull/<N>/head is fetched as FETCH_HEAD and points at
        # another. That is the force-push window the pin exists to close.
        Path(".git", "FETCH_HEAD").write_text("%s\t\tbranch 'main' of example\n" % self.base,
                                              encoding="utf-8")
        event = Path("event.json")
        event.write_text(json.dumps({"pull_request": pr_dict(
            "Add Example Two", body_from_template(), head="0" * 40, base=self.base)}),
            encoding="utf-8")
        with mock.patch.object(sys, "argv", ["check_pr.py", "--event", str(event)]):
            with self.assertRaises(SystemExit) as caught:
                check_pr.main()
        self.assertIn("moved or mismatched", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
