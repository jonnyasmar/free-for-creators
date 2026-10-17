#!/usr/bin/env python3
"""Print the numbers and tables that promotional write-ups quote. Stdlib only. License: CC0.

Blog posts and social threads about this repo all quote the same handful of figures — entry
count, how many are usable in monetized work, how many need no account — and every one of
them goes stale the week the data grows. An article published at 181 entries was still
advertising 181 nine days and nineteen entries later, and a reviewer on a 35k-star list
rejected a submission for exactly that reason.

So the figures are derived, not typed:

    python3 scripts/promo_stats.py                 # the stats block, for a 数据口径 line
    python3 scripts/promo_stats.py --zero-table    # the "no strings attached" table, Markdown
    python3 scripts/promo_stats.py --json          # machine-readable, for other tooling

The zero-threshold set is the one worth writing about: entries that are simultaneously
usable in monetized work, need no attribution, and need no account. Nothing to track, nothing
to credit, nothing to sign up for.
"""

import argparse
import glob
import json
import os
import sys
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from build_readme import CATEGORY_ORDER, compute_stats  # noqa: E402

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")


def load():
    by_slug = {}
    for path in sorted(glob.glob(os.path.join(DATA_DIR, "*.json"))):
        with open(path, encoding="utf-8") as fh:
            doc = json.load(fh)
        by_slug[doc["slug"]] = doc
    return by_slug


def domain(url):
    """pixabay.com from https://www.pixabay.com/music/ — the shortest honest link label."""
    host = urlsplit(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def is_zero_threshold(entry):
    return (entry.get("monetization") == "allowed"
            and entry.get("attribution") == "not-required"
            and entry.get("signup") == "not-required")


def zero_threshold(by_slug):
    """Return [(slug, category_zh, entry), ...] in the repo's category order."""
    out = []
    for slug in CATEGORY_ORDER:
        doc = by_slug.get(slug)
        if not doc:
            continue
        for entry in doc.get("entries", []):
            if is_zero_threshold(entry):
                out.append((slug, doc.get("category_zh") or doc.get("category"), entry))
    return out


def stats(by_slug):
    categories = [by_slug[s] for s in CATEGORY_ORDER if s in by_slug]
    total = sum(len(c.get("entries", [])) for c in categories)
    s = compute_stats(categories)
    s["total"] = total
    s["categories"] = len(categories)
    s["zero_threshold"] = len(zero_threshold(by_slug))
    return s


def render_stats_block(s):
    return ("数据口径（生成于 scripts/promo_stats.py，勿手填）："
            "%d 分类 · %d 资源 · %d 条可货币化 · %d 条无需署名 · %d 条无需注册 · "
            "%d 条整站 CC0/公有领域 · 另 %d 条含 CC0 素材 · %d 条「零门槛」"
            % (s["categories"], s["total"], s["monetizable"], s["no_attribution"],
               s["no_signup"], s["open"], s["open_mixed"], s["zero_threshold"]))


def render_zero_table(by_slug):
    rows = zero_threshold(by_slug)
    out = ["## %d 个「零门槛」精选：无需署名、无需注册、可货币化" % len(rows), ""]
    total = sum(len(d.get("entries", [])) for d in by_slug.values())
    out.append("下面是全部 %d 个资源中，**同时满足**「可货币化 ＋ 无需署名 ＋ 无需注册」三个条件的 "
               "%d 个——下载即用，不留任何尾巴。" % (total, len(rows)))
    by_cat = {}
    for slug, cat_zh, entry in rows:
        by_cat.setdefault((slug, cat_zh), []).append(entry)
    for slug in CATEGORY_ORDER:
        group = [(c, es) for (s, c), es in by_cat.items() if s == slug]
        if not group:
            continue
        cat_zh, entries = group[0]
        out += ["", "### %s（%d 个）" % (cat_zh, len(entries)), "",
                "| 资源 | 许可 | 链接 |", "| --- | --- | --- |"]
        for e in entries:
            host = domain(e["url"])
            out.append("| %s | %s | [%s](%s) |" % (e["name"], e["license"], host, e["url"]))
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zero-table", action="store_true", help="print the Markdown table")
    ap.add_argument("--json", action="store_true", help="print the stats as JSON")
    args = ap.parse_args()

    by_slug = load()
    s = stats(by_slug)
    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.zero_table:
        print(render_zero_table(by_slug))
    else:
        print(render_stats_block(s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
