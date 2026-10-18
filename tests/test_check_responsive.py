"""Unit tests for the responsive gate (scripts/check_responsive.py).

The check's value is entirely in which (page, width, view) cases it drives: a breakpoint
nobody probes is a page nobody opened, which is the bug class the whole file exists for.
These tests pin the case list rather than the browser work. License: CC0.
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import check_responsive  # noqa: E402


class WidthTests(unittest.TestCase):
    def test_every_breakpoint_is_probed_with_both_neighbours(self):
        # An off-by-one at a media query is where this bug lives, so 759/760/761 matters
        # more than 760 alone.
        for triple in ((559, 560, 561), (719, 720, 721), (759, 760, 761), (899, 900, 901),
                       (979, 980, 981)):
            for width in triple:
                self.assertIn(width, check_responsive.WIDTHS, triple)

    def test_phone_and_desktop_ends_are_covered(self):
        self.assertIn(320, check_responsive.WIDTHS)
        self.assertIn(1280, check_responsive.WIDTHS)

    def test_widths_are_sorted_and_unique(self):
        self.assertEqual(check_responsive.WIDTHS, sorted(set(check_responsive.WIDTHS)))


class FrameTests(unittest.TestCase):
    def test_each_page_is_measured_at_its_own_width_set(self):
        frames = check_responsive.frames_for([320, 1280])
        for page, views, width_set, _ in check_responsive.PAGES:
            got = sorted(f[0] for f in frames if f[1] == page)
            want = [320, 1280] if width_set == "all" else [320, 768, 1280]
            self.assertEqual(got, want, page)

    def test_cases_expand_every_view(self):
        cases = check_responsive.probe_cases(check_responsive.frames_for([320]))
        index = [c["view"] for c in cases if c["page"] == "index.html"]
        self.assertEqual(sorted(index), ["grid", "table"])
        self.assertEqual(len([c for c in cases if c["page"] == "all.html"]), 1)

    def test_pages_that_do_not_read_the_payload_are_not_expected_to_render_entries(self):
        # A page that renders no entries cannot overflow, so "expect" is what stops this
        # check passing vacuously on a page whose payload never arrived.
        cases = check_responsive.probe_cases(check_responsive.frames_for([320]))
        for page in ("404.html", "changelog.html"):
            self.assertTrue(all(not c["expect"] for c in cases if c["page"] == page), page)
        for page in ("index.html", "all.html"):
            self.assertTrue(all(c["expect"] for c in cases if c["page"] == page), page)


class PageCoverageTests(unittest.TestCase):
    def test_every_html_page_in_site_is_listed(self):
        site = check_responsive.DEFAULT_SITE
        if not site.is_dir():
            self.skipTest("site/ is not built in this checkout")
        listed = {page for page, _, _, _ in check_responsive.PAGES}
        on_disk = {p.name for p in sorted(site.glob("*.html"))}
        self.assertTrue(on_disk <= listed,
                        "unlisted page(s) would never be measured: %s" % sorted(on_disk - listed))

    def test_pages_have_a_known_width_set(self):
        for page, views, width_set, expects in check_responsive.PAGES:
            self.assertIn(width_set, ("all", "static"), page)
            self.assertTrue(views, page)
            self.assertIsInstance(expects, bool, page)


if __name__ == "__main__":
    unittest.main()
