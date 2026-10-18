"""Unit tests for the llms.txt generator (scripts/build_llms.py).

Its whole job is to restate facts that live elsewhere — counts, category totals, the meaning
of each enum — so the failure mode is not a crash but a model quoting a number the README
contradicts. The enum tables are checked against the schema for exactly that reason.
License: CC0.
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_llms  # noqa: E402
import build_readme  # noqa: E402

ENTRY = {
    "name": "Nord",
    "url": "https://www.nordtheme.com/",
    "description": "16-colour arctic palette.",
    "license": "MIT",
    "license_family": "permissive",
    "attribution": "not-required",
    "monetization": "allowed",
    "signup": "not-required",
}


class EnumCoverageTests(unittest.TestCase):
    """Every enum value the schema allows must have a plain-English phrase here."""

    def test_attribution_is_covered(self):
        self.assertEqual(set(build_llms.ATTRIBUTION), build_readme.ATTRIBUTION_VALUES)

    def test_monetization_is_covered(self):
        self.assertEqual(set(build_llms.MONETIZATION), build_readme.MONETIZATION_VALUES)

    def test_signup_is_covered(self):
        self.assertEqual(set(build_llms.SIGNUP), build_readme.SIGNUP_VALUES)


class EntryLineTests(unittest.TestCase):
    def test_line_carries_all_four_answers_and_the_url(self):
        line = build_llms.entry_line(ENTRY)
        self.assertIn("**Nord**", line)
        self.assertIn("license: MIT", line)
        self.assertIn("no attribution needed", line)
        self.assertIn("safe in monetized content", line)
        self.assertIn("no account needed", line)
        self.assertTrue(line.endswith(ENTRY["url"]), line)

    def test_a_trailing_full_stop_is_dropped_so_the_line_stays_one_sentence(self):
        self.assertNotIn("palette. |", build_llms.entry_line(ENTRY))

    def test_an_unknown_signup_does_not_render_as_a_python_artifact(self):
        self.assertIn("sign-up unknown", build_llms.entry_line(dict(ENTRY, signup=None)))


class ShippedDataTests(unittest.TestCase):
    def test_categories_are_loaded_in_the_readme_order(self):
        cats = build_llms.load_categories()
        self.assertEqual([c["slug"] for c in cats], build_readme.CATEGORY_ORDER)

    def test_every_shipped_entry_can_be_rendered_as_a_line(self):
        for cat in build_llms.load_categories():
            for entry in cat["entries"]:
                self.assertTrue(build_llms.entry_line(entry).startswith("- **"), entry["name"])


if __name__ == "__main__":
    unittest.main()
