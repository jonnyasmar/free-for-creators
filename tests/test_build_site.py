"""Unit tests for the site generator (scripts/build_site.py) and its two history outputs.

The feed and the changelog are the only artifacts here derived from git history rather than
from data/, which makes them the easiest to get subtly wrong: an unescaped ampersand in a
category name, a commit that touched data/ but changed no entry, a new category whose
entries never appear because the file itself was the only thing the diff saw. Stdlib only.
License: CC0.
"""
import json
import os
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import build_site  # noqa: E402

COMMITS = [
    {"sha": "a" * 40, "date": "2026-10-18", "subject": "data: add two entries",
     "changes": [
         {"kind": "added", "file": "colors.json", "entry": {"name": "Nord", "url": "https://x/"},
          "category": "Colors & Gradients"},
         {"kind": "corrected", "file": "fonts.json",
          "entry": {"name": "Lexend", "url": "https://y/"}, "category": "Fonts"},
     ]},
    {"sha": "b" * 40, "date": "2026-10-11", "subject": "data: remove one",
     "changes": [{"kind": "removed", "file": "music.json",
                  "entry": {"name": "Gone", "url": "https://z/"}, "category": "Music"}]},
]


class EscapingTests(unittest.TestCase):
    def test_esc_covers_the_four_characters_that_break_markup(self):
        self.assertEqual(build_site.esc('a & b < c > d "e"'), "a &amp; b &lt; c &gt; d &quot;e&quot;")


class CatColorTests(unittest.TestCase):
    INDEX = '<script>const CAT_COLORS = { "music": "#111", "fonts": "#222" };'

    def test_accepts_full_coverage(self):
        self.assertEqual(build_site.check_cat_colors(self.INDEX, ["music", "fonts"]), [])

    def test_reports_a_missing_slug(self):
        problems = build_site.check_cat_colors(self.INDEX, ["music", "fonts", "colors"])
        self.assertTrue(any("colors" in p for p in problems), problems)

    def test_reports_a_slug_that_no_longer_exists(self):
        problems = build_site.check_cat_colors(self.INDEX, ["music"])
        self.assertTrue(any("not in CATEGORY_ORDER" in p for p in problems), problems)

    def test_reports_an_unparseable_map(self):
        problems = build_site.check_cat_colors("<script>const NOPE = {};</script>", ["music"])
        self.assertEqual(len(problems), 1)


class SitemapTests(unittest.TestCase):
    def test_lists_every_page_and_the_feed(self):
        xml = build_site.render_sitemap("2026-10-18")
        for url in ("", "all.html", "changelog.html", "feed.xml"):
            self.assertIn("%s%s<" % (build_site.PAGES_URL, url), xml.replace("</loc>", "<"))
        self.assertEqual(xml.count("<lastmod>2026-10-18</lastmod>"), 4)


class ChangelogSentenceTests(unittest.TestCase):
    def test_sentences_name_the_resource_and_the_category(self):
        self.assertEqual(
            build_site.change_sentence(COMMITS[0]["changes"][0]),
            "Added Nord to Colors & Gradients")

    def test_corrected_and_removed_read_differently(self):
        self.assertTrue(build_site.change_sentence(COMMITS[0]["changes"][1]).startswith("Corrected"))
        self.assertTrue(build_site.change_sentence(COMMITS[1]["changes"][0]).startswith("Removed"))

    def test_a_category_only_change_says_so(self):
        change = {"kind": "category-added", "file": "colors.json", "entry": None,
                  "category": "Colors & Gradients"}
        self.assertEqual(build_site.change_sentence(change), "Added the Colors & Gradients category")

    def test_title_is_a_sentence_for_one_change_and_a_summary_for_many(self):
        self.assertEqual(build_site.feed_entry_title(COMMITS[0]["changes"][:1]),
                         "Added Nord to Colors & Gradients")
        self.assertEqual(build_site.feed_entry_title(COMMITS[0]["changes"]),
                         "2 entry changes in Colors & Gradients, Fonts")


class FeedTests(unittest.TestCase):
    def test_feed_is_well_formed_and_escapes_ampersands(self):
        xml = build_site.render_feed(COMMITS, "2026-10-18")
        root = ET.fromstring(xml)
        ns = "{http://www.w3.org/2005/Atom}"
        self.assertEqual(root.tag, ns + "feed")
        titles = [e.find(ns + "title").text for e in root.findall(ns + "entry")]
        self.assertEqual(titles[0], "2 entry changes in Colors & Gradients, Fonts")
        # Escaped on the wire, and parsed back to a single ampersand — not "&amp;amp;".
        self.assertIn("Colors &amp; Gradients", xml)
        self.assertIn("Colors & Gradients", titles[0])

    def test_feed_links_each_commit(self):
        xml = build_site.render_feed(COMMITS, "2026-10-18")
        self.assertIn("https://github.com/%s/commit/%s" % (build_site.REPO, "a" * 40), xml)

    def test_updated_comes_from_the_newest_commit_not_the_clock(self):
        xml = build_site.render_feed(COMMITS, "2020-01-01")
        root = ET.fromstring(xml)
        ns = "{http://www.w3.org/2005/Atom}"
        self.assertEqual(root.find(ns + "updated").text, "2026-10-18T00:00:00Z")

    def test_feed_caps_its_entries(self):
        many = [dict(COMMITS[0], sha="%040d" % i) for i in range(build_site.FEED_ENTRIES + 20)]
        root = ET.fromstring(build_site.render_feed(many, "2026-10-18"))
        ns = "{http://www.w3.org/2005/Atom}"
        self.assertEqual(len(root.findall(ns + "entry")), build_site.FEED_ENTRIES)

    def test_an_empty_history_still_produces_a_valid_feed(self):
        root = ET.fromstring(build_site.render_feed([], "2026-10-18"))
        ns = "{http://www.w3.org/2005/Atom}"
        self.assertEqual(root.findall(ns + "entry"), [])


class ChangelogPageTests(unittest.TestCase):
    def test_page_lists_every_change_and_is_escaped(self):
        html = build_site.render_changelog(COMMITS, 214, "2026-10-18")
        self.assertIn("What&#39;s new", html)
        self.assertIn("Added Nord to Colors &amp; Gradients", html)
        self.assertIn("2026-10-11", html)
        self.assertIn("do not edit by hand", html)
        self.assertIn('class="changes"', html)

    def test_empty_history_explains_itself_rather_than_rendering_blank(self):
        html = build_site.render_changelog([], 214, "2026-10-18")
        self.assertIn("shallow clone", html)


class ShippedDataIntegrationTests(unittest.TestCase):
    def test_payloads_cover_every_slug_and_a_total_that_matches(self):
        cats = build_site.load_categories()
        payload = build_site.payloads(cats)
        for slug in build_site.CATEGORY_ORDER:
            self.assertIn("%s.json" % slug, payload)
        all_json = json.loads(payload["all.json"])
        entries = sum(len(c["entries"]) for c in cats)
        self.assertEqual(all_json["total"], entries)
        self.assertEqual([c["slug"] for c in all_json["categories"]],
                         list(build_site.CATEGORY_ORDER))

    def test_jsonld_carries_the_real_total(self):
        cats = build_site.load_categories()
        total = sum(len(c["entries"]) for c in cats)
        ld = build_site.jsonld(cats, total, "2026-10-18")
        self.assertEqual(ld["@graph"][0]["numberOfItems"], total)

    def test_history_is_newest_first_and_every_change_knows_its_category(self):
        commits = build_site.data_history(limit=25)
        self.assertTrue(commits, "the repository should have data/ history")
        dates = [c["date"] for c in commits]
        self.assertEqual(dates, sorted(dates, reverse=True))
        for commit in commits:
            self.assertTrue(commit["changes"], "a commit with no entry change is filtered out")
            for change in commit["changes"]:
                self.assertTrue(change.get("category"), change)
                self.assertIn(change["kind"], {"added", "corrected", "removed"})

    def test_social_preview_png_size_is_read_from_the_file(self):
        self.assertEqual(build_site.png_size(build_site.SOCIAL_PNG_SRC), (1280, 640))


if __name__ == "__main__":
    unittest.main()
