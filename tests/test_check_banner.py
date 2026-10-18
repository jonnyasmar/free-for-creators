"""Unit tests for the banner layout check (scripts/check_banner.py).

The rules are what a picture cannot tell you: the banner draws one chip per category in a
flex row with a fixed max-width, so adding a category moves geometry and nothing else. Every
rule below is one way that can go wrong without a single pixel changing colour.
License: CC0.
"""
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_banner  # noqa: E402


def chips(count=11, rows=3, chip_height=29, row_gap=9, width=620, left=72, top=315,
          scroll_extra=0):
    height = rows * chip_height + (rows - 1) * row_gap
    return {
        "chipCount": count,
        "chipHeight": chip_height,
        "chips": {"top": top, "bottom": top + height, "left": left, "right": left + width,
                  "width": width, "height": height,
                  "clientWidth": width, "scrollWidth": width + scroll_extra, "rowGap": row_gap},
        "card": {"top": 98, "bottom": 423, "left": 736, "right": 1220, "width": 484,
                 "height": 325, "clientWidth": 484, "scrollWidth": 484, "rowGap": 0},
    }


def failures(metrics, expected=11):
    return check_banner.evaluate(metrics, expected)[0]


class AcceptTests(unittest.TestCase):
    def test_the_shipped_layout_passes(self):
        self.assertEqual(failures(chips()), [])

    def test_notes_report_the_row_count_and_the_gap(self):
        notes = check_banner.evaluate(chips(), 11)[1]
        self.assertTrue(any("3 row(s)" in n for n in notes), notes)
        self.assertTrue(any("gap to the table card" in n for n in notes), notes)


class ChipCountTests(unittest.TestCase):
    def test_a_category_without_a_chip_fails(self):
        problems = failures(chips(count=10), expected=11)
        self.assertTrue(any("add the missing chip" in p for p in problems), problems)

    def test_a_chip_without_a_category_fails(self):
        self.assertTrue(failures(chips(count=12), expected=11))


class RowBudgetTests(unittest.TestCase):
    def test_a_fourth_row_needs_a_deliberate_review(self):
        problems = failures(chips(rows=4), expected=11)
        self.assertTrue(any("MAX_CHIP_ROWS" in p for p in problems), problems)

    def test_the_budget_is_stated_in_the_message(self):
        problems = failures(chips(rows=5), expected=11)
        self.assertTrue(any("budget 3" in p for p in problems), problems)


class GeometryTests(unittest.TestCase):
    def test_overlowing_the_row_box_fails(self):
        problems = failures(chips(scroll_extra=40))
        self.assertTrue(any("overflows" in p for p in problems), problems)

    def test_a_row_that_runs_past_the_bottom_margin_fails(self):
        problems = failures(chips(top=580, rows=2))
        self.assertTrue(any("bottom margin" in p for p in problems), problems)

    def test_a_row_that_collides_with_the_table_card_fails(self):
        metrics = chips()
        metrics["card"]["left"] = 600  # the card moved left, into the chips' column
        problems = failures(metrics)
        self.assertTrue(any("overlap" in p for p in problems), problems)

    def test_a_row_outside_the_artboard_fails(self):
        metrics = chips(left=1000)
        problems = failures(metrics)
        self.assertTrue(any("leaves the artboard" in p for p in problems), problems)


class MissingPartsTests(unittest.TestCase):
    def test_a_missing_chips_block_is_named(self):
        metrics = chips()
        del metrics["chips"]
        self.assertTrue(any(".chips" in p for p in failures(metrics)))

    def test_a_missing_table_card_is_named(self):
        metrics = chips()
        del metrics["card"]
        self.assertTrue(any(".table-card" in p for p in failures(metrics)))


class CategoryCountTests(unittest.TestCase):
    def test_counts_the_category_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("music.json", "fonts.json", "notes.txt"):
                Path(tmp, name).write_text("{}", encoding="utf-8")
            self.assertEqual(check_banner.category_count(tmp), 2)

    def test_matches_the_shipped_data(self):
        self.assertEqual(check_banner.category_count(), len(list((ROOT / "data").glob("*.json"))))


def chrome():
    return shutil.which("google-chrome") or shutil.which("chromium") or shutil.which("chrome") or (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        if Path("/Applications/Google Chrome.app").exists() else None)


class LiveRenderTests(unittest.TestCase):
    """The probe and the DOM read-back, against the real artwork."""

    @unittest.skipUnless(chrome(), "no Chrome/Chromium available")
    def test_measure_reads_the_real_artwork(self):
        metrics = check_banner.measure(check_banner.HTML, chrome())
        self.assertGreaterEqual(metrics["chipCount"], 1)
        self.assertGreater(metrics["chips"]["height"], 0)
        self.assertGreater(metrics["card"]["left"], metrics["chips"]["right"])

    @unittest.skipUnless(chrome(), "no Chrome/Chromium available")
    def test_the_real_artwork_passes_its_own_rules(self):
        metrics = check_banner.measure(check_banner.HTML, chrome())
        self.assertEqual(check_banner.evaluate(metrics, check_banner.category_count())[0], [])


if __name__ == "__main__":
    unittest.main()
