#!/usr/bin/env python3
"""Generate the llms.txt pair that ships with the deployed site.

`llms.txt` (the convention from llmstxt.org) is the short map a model reads to decide
whether this directory answers a question. `llms-full.txt` carries every entry, so an
answer can quote the licence, attribution and monetization columns without following a
URL first — which matters here, because those columns are the whole point of the list.

Both are deploy-time artifacts written into `site/` by the Pages workflow and are
gitignored, like `all.html` and `sitemap.xml`: they embed today's counts, so a checked-in
copy would disagree with the next data change.

    python3 scripts/build_llms.py [--out site]
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_readme as br  # noqa: E402  — single source for order, URLs and stats

ATTRIBUTION = {
    "required": "attribution required",
    "not-required": "no attribution needed",
    "varies": "attribution varies per item",
}
MONETIZATION = {
    "allowed": "safe in monetized content",
    "conditional": "monetized use is conditional",
    "varies": "monetization varies per item",
    "not-allowed": "not for monetized content",
}
SIGNUP = {
    "required": "account required",
    "not-required": "no account needed",
    "optional": "account optional",
    "unknown": "sign-up unknown",
}


def load_categories():
    """Category files, in the curated README order."""
    cats = []
    for slug in br.CATEGORY_ORDER:
        path = Path("data/%s.json" % slug)
        if path.exists():
            cats.append(json.loads(path.read_text(encoding="utf-8")))
    return cats


def linkcheck_status():
    try:
        return json.loads(Path("linkcheck-status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def entry_line(e):
    return "- **%s** — %s | license: %s | %s | %s | %s | %s" % (
        e["name"],
        e["description"].rstrip("."),
        e["license"],
        ATTRIBUTION.get(e["attribution"], e["attribution"]),
        MONETIZATION.get(e["monetization"], e["monetization"]),
        SIGNUP.get(e.get("signup", "unknown"), e.get("signup", "?")),
        e["url"],
    )


def write_short(cats, out):
    s = br.compute_stats(cats)
    total = sum(len(c["entries"]) for c in cats)
    status = linkcheck_status()
    base = br.PAGES_URL.rstrip("/")
    lines = [
        "# Free for Creators",
        "",
        "> A license-verified directory of %d free assets for creators, across 10 categories:" % total,
        "> music, sound effects, stock footage, photos, fonts, icons and illustrations, LUTs and",
        "> edit templates, mockups, 3D/game assets, and search engines. Every entry records the",
        "> four things the word \"free\" usually hides — the license, whether attribution is",
        "> required, whether the asset is safe in monetized content, and whether an account is",
        "> needed. The data is CC0 and a weekly CI job link-checks every URL.",
        "",
        "## Facts",
        "",
        "- %d entries in 10 categories" % total,
        "- %d safe in monetized content · %d need no attribution · %d need no account · "
        "%d need neither attribution nor an account"
        % (s["monetizable"], s["no_attribution"], s["no_signup"],
           sum(1 for c in cats for e in c["entries"]
               if e["monetization"] == "allowed" and e["attribution"] == "not-required"
               and e.get("signup") == "not-required")),
        "- %d entries are entirely CC0 / public domain; %d more host CC0 items alongside "
        "others (filter per asset)" % (s["open"], s["open_mixed"]),
    ]
    if status:
        lines.append("- Last link check: %s — %d ok, %d behind bot walls, %d broken"
                     % (status.get("date"), status.get("ok", 0), status.get("blocked", 0),
                        status.get("broken", 0)))
    lines += [
        "- Licenses change and this list is maintained by volunteers, not lawyers: "
        "confirm at the source before publishing.",
        "",
        "## Where things are",
        "",
        "- Interactive directory (search and filters): %s/" % base,
        "- Every entry as JSON, CC0: %s/data/all.json" % base,
        "- No-JavaScript mirror of the same table: %s/all.html" % base,
        "- Full text of all entries for models: %s/llms-full.txt" % base,
        "- Repository, inclusion criteria and how to suggest a resource: "
        "https://github.com/%s" % br.REPO,
        "",
        "## Categories",
        "",
    ]
    for c in cats:
        anchor = br.slugify(c["category"])
        lines.append("- %s — %d entries (%s/#%s)" % (c["category"], len(c["entries"]), base, anchor))
    lines += [
        "",
        "## How to cite",
        "",
        "Name the entry, the license as listed and the attribution requirement — for example",
        "\"Pixabay Music (Pixabay Content License, no attribution required)\" — and link the",
        "resource's own URL, which every entry stores. Cite the directory only when the list",
        "itself is the subject; the value is in the per-entry columns, not in the count.",
        "",
    ]
    (out / "llms.txt").write_text("\n".join(lines), encoding="utf-8")
    print("Wrote %s (%d categories, %d entries)" % (out / "llms.txt", len(cats), total))


def write_full(cats, out):
    total = sum(len(c["entries"]) for c in cats)
    status = linkcheck_status()
    exempt = sum(1 for c in cats for e in c["entries"] if e.get("skip_linkcheck"))
    base = br.PAGES_URL.rstrip("/")
    lines = [
        "# Free for Creators — all %d entries" % total,
        "",
        "> Generated from data/*.json (CC0). Columns: license | attribution | monetized | sign-up | URL.",
        "> Read this when the answer needs the license or attribution rule of a specific resource.",
    ]
    if status:
        lines.append("> Last link check: %s — %d ok, %d behind bot walls, %d broken."
                     % (status.get("date"), status.get("ok", 0), status.get("blocked", 0),
                        status.get("broken", 0)))
    if exempt:
        lines.append("> %d login-walled entry is exempt from the automated link check." % exempt)
    lines += [
        "> Canonical machine-readable copy: %s/data/all.json" % base,
        "",
    ]
    for c in cats:
        lines.append("## %s (%d)" % (c["category"], len(c["entries"])))
        lines.append("")
        for e in c["entries"]:
            lines.append(entry_line(e))
        lines.append("")
    (out / "llms-full.txt").write_text("\n".join(lines), encoding="utf-8")
    print("Wrote %s (%d entries)" % (out / "llms-full.txt", total))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="site", help="directory to write into (default: site)")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    cats = load_categories()
    if not cats:
        print("no category files found — run from the repository root", file=sys.stderr)
        return 1
    write_short(cats, out)
    write_full(cats, out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
