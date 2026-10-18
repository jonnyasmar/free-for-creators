#!/usr/bin/env python3
"""Fail CI when a deployed page scrolls sideways at any viewport width we support.

A static page has no build step that can see this class of bug. The CSS looks right at
the desktop widths a developer uses, and the page only breaks at a boundary. Two did:

* `site/all.html` — a table cannot shrink below its own min-content width (~520-630px
  with the secondary columns hidden), and nothing wrapped it in a scroll container, so a
  320px phone got a 649px document: 329px of sideways scroll on every table.
* `site/index.html` — the table view kept `min-width: 900px` because the
  `@media (max-width: 760px)` override was written as `.tbl` and lost the specificity
  fight against the base rule `table.tbl`. A 768px iPad scrolled 98px sideways, in that
  page's *default* view for its width.

Neither is visible in a build log, a diff, or the desktop screenshot attached to a PR.
This drives the real pages in headless Chrome and asks each one the only question that
settles it: how far can the document itself scroll horizontally?

The width list is every breakpoint in the stylesheets plus both neighbours. Off-by-one at
a media query is where this bug lives: 759/760/761 has caught more than 1000 ever has.

Stdlib only, plus a Chrome/Chromium binary:

    python3 scripts/build_site.py            # once, so site/data/ exists
    python3 scripts/check_responsive.py      # auto-detects Chrome
    python3 scripts/check_responsive.py --chrome /usr/bin/google-chrome
    python3 scripts/check_responsive.py --site /tmp/preview --verbose
"""

from __future__ import annotations

import argparse
import base64
import functools
import http.server
import json
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SITE = ROOT / "site"

# Served from memory, on the same origin as the pages, so the probe can read the iframes'
# documents. Never written into site/, which is uploaded whole as the deploy artifact.
PROBE_PATH = "/__responsive_probe.html"

# Rounding and font-metric drift are not bugs; 98px is. This runs on a Linux runner while
# the fixes get written on macOS, and the two resolve different fonts for the same stack, so
# a pixel or two of difference in a text box is normal. The smallest real failure was 7px
# (the phone grid) and the rest were 47/98/329/577px, so 2px keeps every one of them while
# giving the font substitution somewhere to land.
TOLERANCE_PX = 2

WIDTHS = [
    # phones
    280, 320, 360, 375, 414, 480,
    # every breakpoint in the stylesheets, with both neighbours
    559, 560, 561,      # .tb padding, #bento 5 -> 2 columns
    639, 640,           # index.html's client-side default view (grid below 640)
    719, 720, 721,      # all.html cell padding
    759, 760, 761,      # chip rails, .grid 1 column, .tbl column hiding
    899, 900, 901,      # table column sets (site/index.html, site/all.html)
    979, 980, 981,      # #bento 3 -> 5 columns
    # desktops
    1024, 1280,
]

# Every page in site/, with how to drive it: the views to measure (None = the page has no
# client-side toggle) and which widths to use. main() fails if site/ contains an .html file
# that is not listed here, so adding a page is a decision rather than a silent gap — the
# whole point of this check is that a page nobody opened is where the bug hides.
PAGES = [
    ("index.html", ["table", "grid"], "all"),
    ("all.html", [None], "all"),
    ("404.html", [None], "static"),  # renders nothing client-side: a few widths will do
    ("changelog.html", [None], "static"),  # prose and lists, no table
]


def frames_for(widths):
    """One iframe per (page, width); index.html is re-measured in place for its second view,
    because loading it costs a full 200-entry render."""
    frames = []
    for page, views, width_set in PAGES:
        for width in (widths if width_set == "all" else (320, 768, 1280)):
            frames.append((width, page, views))
    return frames


def probe_cases(frames):
    cases = []
    for width, page, views in frames:
        for view in views:
            cases.append({
                "page": page,
                "view": view,
                "width": width,
                # A page that rendered no entries cannot overflow, which is how this check
                # would pass on a site whose payload failed to build.
                "expect": page != "404.html",
            })
    return cases


PROBE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>probe</title></head>
<body>
<pre id="ffc-probe">PENDING</pre>
<script>
var CASES = __CASES__;
var TOLERANCE = __TOLERANCE__;

// Only ever runs on a failing case. CI runs Linux with different font metrics from the
// machine a fix was written on, so "the document is 3px wider than the viewport" without a
// name is a failure nobody can act on remotely; the element name is what makes it fixable.
function nameOf(el) {
  var cls = (typeof el.className === 'string' ? el.className.trim().split(/\s+/).slice(0, 2).join('.') : '');
  return el.tagName.toLowerCase() + (el.id ? '#' + el.id : '') + (cls ? '.' + cls : '');
}

function clipperOf(el, de) {
  var a = el.parentElement;
  while (a && a !== de) {
    var ox = getComputedStyle(a).overflowX;
    if (ox === 'hidden' || ox === 'auto' || ox === 'scroll' || ox === 'clip') return a;
    a = a.parentElement;
  }
  return null;
}

function offenders(win, vw) {
  var de = win.document.documentElement;
  var all = win.document.body.querySelectorAll('*');
  var open = [];
  for (var i = 0; i < all.length; i++) {
    var el = all[i];
    if (el.tagName === 'HTML' || el.tagName === 'BODY') continue;
    var r = el.getBoundingClientRect();
    if (r.width === 0 && r.height === 0) continue;
    if (r.right <= vw + 1) continue;
    // Unclipped and past the viewport: the culprit, by definition. Anything clipped is
    // skipped on purpose — the decorative blobs live in a fixed, clipped .bg and the chip
    // rail is a deliberate horizontal scroller, so both stick out of a phone viewport
    // without ever moving the page, and they would be named on nearly every failure.
    if (clipperOf(el, de)) continue;
    open.push({ right: r.right, text: nameOf(el) + '[w=' + Math.round(r.width) + ',right=' + Math.round(r.right) + ']' });
  }
  open.sort(function (a, b) { return b.right - a.right; });
  if (!open.length) return '';
  return 'elements past the viewport: ' + open.slice(0, 3).map(function (p) { return p.text; }).join(', ');
}

// When nothing explains the overflow from the outside, measure instead of guessing: hide
// one child at a time and keep descending into whichever branch shrinks the document. It
// costs a few dozen forced layouts on a 30k-pixel-tall page, so it runs for the first
// unattributed failure only.
async function bisect(win) {
  var de = win.document.documentElement;
  var base = de.scrollWidth;
  var path = [];
  // Hiding an element inside an iframe does not always change scrollWidth on the next
  // synchronous read: the overflow arrives with the compositor. Yield a frame between
  // steps, with a timeout floor so a throttled frame cannot hang the whole run.
  function settle() {
    return new Promise(function (resolve) {
      var done = false;
      function finish() { if (!done) { done = true; resolve(); } }
      requestAnimationFrame(finish);
      setTimeout(finish, 50);
    });
  }
  async function probe(el) {
    for (var i = 0; i < el.children.length; i++) {
      var k = el.children[i];
      var prev = k.style.display;
      k.style.display = 'none';
      await settle();
      var sw = de.scrollWidth;
      k.style.display = prev;
      await settle();
      if (sw < base) { path.push(nameOf(k)); await probe(k); return; }
    }
  }
  await probe(win.document.body);
  if (!path.length) return '';
  return 'removing ' + path.join(' > ') + ' shrinks the document from ' + base + 'px back to the viewport';
}

function frame(url, width) {
  return new Promise(function (resolve, reject) {
    var f = document.createElement('iframe');
    f.setAttribute('width', String(width));
    f.setAttribute('height', '900');
    f.style.cssText = 'display:block;border:0;width:' + width + 'px;height:900px';
    var timer = setTimeout(function () {
      reject(new Error('iframe load timed out: ' + url + ' @' + width + 'px'));
    }, 20000);
    f.addEventListener('load', function () { clearTimeout(timer); resolve(f); }, { once: true });
    f.src = url;
    document.body.appendChild(f);
  });
}

function sleep(ms) { return new Promise(function (r) { setTimeout(r, ms); }); }

function counts(win) {
  var d = win.document;
  return { rows: d.querySelectorAll('table tr').length, cards: d.querySelectorAll('.card').length };
}

function measure(win) {
  var de = win.document.documentElement;
  var se = win.document.scrollingElement || de;
  var before = de.style.scrollBehavior;
  // site/index.html sets `html { scroll-behavior: smooth }`, and a programmatic
  // scrollLeft on a smooth-scrolling document reads back as 0 straight away. Without
  // this line a page with 98px of overflow reports perfect health.
  de.style.scrollBehavior = 'auto';
  se.scrollLeft = 999999;
  var maxX = se.scrollLeft;
  se.scrollLeft = 0;
  de.style.scrollBehavior = before;
  return { maxX: maxX, docWidth: de.scrollWidth, clientWidth: de.clientWidth, innerWidth: win.innerWidth };
}

async function run() {
  var results = [];
  var bisects = 0;
  for (var i = 0; i < CASES.length; i++) {
    var c = CASES[i];
    var row = { page: c.page, view: c.view, width: c.width, expect: c.expect };
    var f = null;
    try {
      f = await frame('/' + c.page, c.width);
      var win = f.contentWindow;
      // The client-rendered pages need data/all.json. A missing payload renders a
      // friendly empty state instead of an error, and an empty page cannot overflow:
      // without this wait the check would pass precisely when the site was broken.
      for (var k = 0; k < 120; k++) {
        var n = counts(win);
        if (n.rows > 0 || n.cards > 0) break;
        await sleep(50);
      }
      if (c.view) {
        var btn = win.document.getElementById(c.view === 'grid' ? 'view-grid' : 'view-table');
        // render() is synchronous, so layout is already up to date; the reads below
        // force it. An extra sleep here would only spend --virtual-time-budget.
        if (btn) { btn.click(); }
        row.viewApplied = !!btn;
      }
      var seen = counts(win);
      row.rows = seen.rows;
      row.cards = seen.cards;
      var m = measure(win);
      row.maxX = m.maxX;
      row.docWidth = m.docWidth;
      row.clientWidth = m.clientWidth;
      row.innerWidth = m.innerWidth;
      if (m.maxX > TOLERANCE) {
        row.culprit = offenders(win, m.innerWidth);
        // Geometry cannot explain every failure (a table can leak out of the scroll
        // container that is supposed to clip it), so fall back to measuring. Bounded to
        // one case per run: it costs a few dozen forced layouts.
        if (!row.culprit && bisects < 1) { row.culprit = await bisect(win); bisects++; }
      }
    } catch (e) {
      row.error = String((e && e.message) || e);
    }
    if (f) { f.remove(); }
    results.push(row);
  }
  document.getElementById('ffc-probe').textContent =
    btoa(unescape(encodeURIComponent(JSON.stringify({ results: results }))));
  document.title = 'PROBE-DONE';
}

run();
</script>
</body></html>
"""


class ProbeHandler(http.server.SimpleHTTPRequestHandler):
    """Serves site/, plus the in-memory probe page on that same origin."""

    def do_GET(self):  # noqa: N802 (stdlib naming)
        if self.path.split("?", 1)[0] == PROBE_PATH:
            body = self.server.probe_html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        super().do_GET()

    def log_message(self, *args):  # keep CI logs to our own output
        pass


def find_chrome(explicit):
    if explicit:
        return explicit
    for name in ("google-chrome", "google-chrome-stable", "chromium-browser", "chromium", "chrome"):
        found = shutil.which(name)
        if found:
            return found
    for path in (
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ):
        if Path(path).exists():
            return path
    return None


def serve(site_dir, probe_html):
    handler = functools.partial(ProbeHandler, directory=str(site_dir))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    httpd.daemon_threads = True
    httpd.probe_html = probe_html
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, port


def dump_dom(chrome, url, budget_ms, timeout):
    with tempfile.TemporaryDirectory(prefix="ffc-probe-") as profile:
        return subprocess.run(
            [
                chrome,
                "--headless=new",
                "--disable-gpu",
                # Containers and some CI images run as root, where the sandbox refuses.
                "--no-sandbox",
                "--disable-dev-shm-usage",
                # Zero-width scrollbars: a classic 15px scrollbar inside an iframe would
                # quietly shrink the width every media query sees, and the whole point of
                # the width list is telling 759, 760 and 761 apart.
                "--hide-scrollbars",
                # Measure a settled page. site/index.html defines animation: none and
                # transition: none under prefers-reduced-motion, so nothing is mid-fade or
                # composited while the widths are read — a transitioning element is the one
                # case where Chrome's overflow reporting gets stateful and two runs of the
                # same width can disagree. It changes no layout, and the reduced-motion
                # rendering is a mode the site explicitly supports. Measured cost of the
                # alternative (default motion): about 8% on this machine, so this is for
                # determinism, not speed.
                "--force-prefers-reduced-motion",
                "--window-size=1600,1000",
                "--virtual-time-budget=%d" % budget_ms,
                "--user-data-dir=" + profile,
                "--dump-dom",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
        )


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--site", default=str(DEFAULT_SITE), help="directory to serve (default: site/)")
    ap.add_argument("--chrome", default=None, help="Chrome/Chromium binary (default: probe PATH)")
    ap.add_argument("--timeout", type=int, default=600, help="seconds to wait for Chrome (default: 600)")
    ap.add_argument("--budget-ms", type=int, default=120000,
                    help="Chrome --virtual-time-budget in ms (default: 120000)")
    ap.add_argument("--widths", default=None,
                    help="comma-separated widths to test instead of the full list (local iteration)")
    ap.add_argument("--verbose", action="store_true", help="print every case, not just failures")
    args = ap.parse_args()

    site_dir = Path(args.site).resolve()
    if not (site_dir / "index.html").exists():
        print("ERROR: %s/index.html not found." % site_dir, file=sys.stderr)
        return 1
    if not (site_dir / "data" / "all.json").exists():
        print("ERROR: %s is missing. The interactive page renders an empty state without"
              " it, which cannot overflow — so this check would pass vacuously."
              % (site_dir / "data" / "all.json"), file=sys.stderr)
        print("       Run: python3 scripts/build_site.py", file=sys.stderr)
        return 1

    known = set(page for page, _, _ in PAGES)
    unknown = sorted(p.name for p in site_dir.glob("*.html") if p.name not in known)
    if unknown:
        print("ERROR: %s contains page(s) this check does not drive: %s" % (site_dir, ", ".join(unknown)),
              file=sys.stderr)
        print("       Add each one to PAGES in scripts/check_responsive.py, with the control"
              " that switches its views (or say why it cannot scroll sideways).", file=sys.stderr)
        return 1

    chrome = find_chrome(args.chrome)
    if not chrome:
        print("ERROR: no Chrome/Chromium binary found. Pass --chrome /path/to/chrome.", file=sys.stderr)
        return 1

    widths = [int(w) for w in args.widths.split(",")] if args.widths else WIDTHS
    frames = frames_for(widths)
    all_cases = probe_cases(frames)
    probe_html = (PROBE_HTML
                  .replace("__CASES__", json.dumps(all_cases))
                  .replace("__TOLERANCE__", str(TOLERANCE_PX)))

    httpd, port = serve(site_dir, probe_html)
    try:
        url = "http://127.0.0.1:%d%s" % (port, PROBE_PATH)
        print("Probing %d page/view/width combinations (%d page loads) on %s"
              % (len(all_cases), len(frames), site_dir))
        try:
            proc = dump_dom(chrome, url, args.budget_ms, args.timeout)
        except subprocess.TimeoutExpired:
            print("ERROR: Chrome did not finish within %ds." % args.timeout, file=sys.stderr)
            return 1
    finally:
        httpd.shutdown()

    match = re.search(r'<pre id="ffc-probe">([A-Za-z0-9+/=]*)</pre>', proc.stdout)
    if not match or match.group(1) in ("", "PENDING"):
        print("ERROR: the probe produced no results (Chrome exit %s)." % proc.returncode, file=sys.stderr)
        for line in (proc.stderr or "").strip().splitlines()[-15:]:
            print("       " + line, file=sys.stderr)
        return 1
    results = json.loads(base64.b64decode(match.group(1)).decode("utf-8"))["results"]
    if len(results) != len(all_cases):
        print("ERROR: the probe returned %d of %d results — it did not finish inside"
              " --virtual-time-budget=%dms. Raise --budget-ms." % (len(results), len(all_cases), args.budget_ms),
              file=sys.stderr)
        return 1

    failures = []
    worst_by_width = dict((w, 0) for w in widths)
    for row in results:
        label = "%s (%s) @%dpx" % (row["page"], row.get("view") or "default", row["width"])
        if args.verbose:
            print("  %-32s rows=%-4s cards=%-4s maxScrollX=%s"
                  % (label, row.get("rows"), row.get("cards"), row.get("maxX")))

        if row.get("error"):
            failures.append("%s: %s" % (label, row["error"]))
            continue
        # The harness has to be measuring the width it thinks it is: a clamped iframe or a
        # stray scrollbar would move every boundary case into a different media query and
        # the run would look green while testing widths nobody asked about.
        if abs(row["clientWidth"] - row["width"]) > 1:
            failures.append("%s: harness laid out a %dpx viewport, not %dpx"
                            % (label, row["clientWidth"], row["width"]))
            continue
        if row["expect"] and not (row.get("rows") or row.get("cards")):
            failures.append("%s: rendered no entries — did the payload build? (site/data/)" % label)
            continue
        if row.get("view") and not row.get("viewApplied"):
            failures.append("%s: no #view-%s control to click" % (label, row["view"]))
            continue
        if row["maxX"] > TOLERANCE_PX:
            why = ("; " + row["culprit"]) if row.get("culprit") else ""
            failures.append("%s: the document scrolls %dpx sideways (document %dpx wide)%s"
                            % (label, row["maxX"], row["docWidth"], why))
            worst_by_width[row["width"]] = max(worst_by_width[row["width"]], row["maxX"])

    if failures:
        print("")
        print("%d of %d combinations scroll sideways or could not be measured:" % (len(failures), len(results)))
        for line in failures:
            print("  FAIL  " + line)
        print("")
        print("Widest sideways scroll per viewport width:")
        for width in sorted(worst_by_width):
            if worst_by_width[width]:
                print("  %5dpx  %4dpx  %s" % (width, worst_by_width[width], "#" * min(40, worst_by_width[width] // 5)))
        print("")
        print("A document wider than its viewport lets the whole layout slide sideways."
              " Fix the overflowing element, do not paper over it with overflow-x: hidden.")
        return 1

    print("OK: %d combinations at %d widths, no page scrolls sideways (widest = its viewport)."
          % (len(results), len(widths)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
