#!/usr/bin/env python3
"""Link-check every URL in data/*.json. Stdlib only (Python >= 3.9). License: CC0.

Classification:
    ok         HTTP 200/2xx-with-content AND the page looks like a real page
    blocked    HTTP 401/403/405/429/999, any 5xx that survives retries, 202/204 (the
               server accepted the request but returned no document), SSL errors,
               timeouts (bot protection), OR a 200 that needs a human: a JS shell with no
               <title>/<h1> in the HTML, or a redirect onto a different domain (rebrand?).
               Needs a human, does NOT fail CI.
    broken     HTTP 404/410, another persistent 4xx, DNS failure, a connection that is
               still refused after retries on BOTH methods, OR a 200 whose own
               <title>/<h1> says "not found" (soft 404).
               Fails CI with exit code 1.

The line between the last two is evidence, not inconvenience. `broken` means the resource
is gone and the entry should be removed, so it is reserved for verdicts that cannot be
explained by a bad day: a 404/410 from the origin, a name that does not resolve, or the
page itself saying "not found". A 5xx is the server's own state and proves nothing about
the resource — fontsquirrel.com answered 502 for an entire run and 202 a minute later,
with the origin merely unwell. Filing a dead-link issue for that would train maintainers
to ignore the issue, which is worse than missing a genuinely dead 5xx; a persistent one
shows up as `blocked` week after week and gets looked at.

A status code alone is not enough: some SPAs answer 200 for any path and render
"Page not found" client-side, and some sites 301 to a new domain after a rebrand.

Usage:
    python3 scripts/linkcheck.py                # check everything
    python3 scripts/linkcheck.py --slug music   # only one category (repeatable)

When $GITHUB_STEP_SUMMARY is set, a Markdown report is appended to it (CI job summary).
"""
import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36 free-for-creators-linkcheck"
)
TIMEOUT = 20
RETRIES = 2

# Set by --no-proxy. build_opener() otherwise inherits the environment's proxies, and on
# macOS that includes a system-wide proxy with no env var set, so `env | grep -i proxy`
# shows nothing while every request still leaves through it. That matters because the
# proxy's exit IP gets bot-walled differently than a direct connection does: of ten
# "HTTP 403" verdicts sampled on a machine using 127.0.0.1:7897, eight were reachable
# straight from the host (two of them plain 200). GitHub Actions runners use no proxy, so
# a local run and CI can disagree for reasons that have nothing to do with the resource.
NO_PROXY = False
MAX_WORKERS = 8

# 5xx is deliberately absent: every 5xx now goes through the retry ladder below and ends
# as `blocked`, so 501/503 no longer need special-casing here (they used to skip retries).
BLOCKED_CODES = {401, 403, 405, 429, 999}

# A 2xx that carries no document. 202 means "accepted, still processing" — what a bot
# queue or challenge returns instead of the page — and 204 is explicitly empty. Neither
# proves the resource is alive, and neither proves it is gone.
CONTENTLESS_OK = {202, 204}

# How much of a GET body to inspect for soft-404 signals. <title> and the first <h1>
# are essentially always inside the first 64 KB of server-rendered HTML.
BODY_BYTES = 65536

# A 200 that is really the site's own "not found" page. Matched against <title> and
# the first <h1> ONLY — scanning the whole body false-positives on legitimate pages
# that merely mention 404.
SOFT_404 = re.compile(
    r"page not found|page (?:cannot|can'?t|could not) be found|can'?t find that|"
    r"doesn'?t exist|does not exist|appears to be moved|error 404|^404\b|404 (?:error|page)",
    re.IGNORECASE,
)


def collect(slugs=None):
    items = []
    for path in sorted(DATA_DIR.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        slug = data.get("slug", path.stem)
        if slugs and slug not in slugs:
            continue
        for e in data.get("entries", []):
            if e.get("skip_linkcheck"):
                continue
            items.append((slug, e.get("name", "?"), e.get("url", ""), bool(e.get("linkcheck_js"))))
    return items


def registrable(host):
    """Crude registrable-domain guess ('www.a.b.co.uk' -> 'co.uk' is wrong, but the
    only thing we care about is 'did the host change identity', e.g. freepik.com ->
    magnific.com, which this catches)."""
    parts = (host or "").lower().split(":")[0].split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else (host or "").lower()


def strip_tags(fragment):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment)).strip()


def inspect_body(body):
    """Return (is_soft_404, title_or_h1_text, had_title_or_h1) for a GET body."""
    ti = re.search(r"<title[^>]*>(.*?)</title>", body, re.S | re.I)
    h1 = re.search(r"<h1[^>]*>(.*?)</h1>", body, re.S | re.I)
    text = " ".join(strip_tags(m.group(1)) for m in (ti, h1) if m).strip()
    return (bool(text) and bool(SOFT_404.search(text))), text[:90], bool(ti or h1)


class Redirect308(urllib.request.HTTPRedirectHandler):
    """Follow 308 as well as 301/302/303/307.

    Python's urllib only learned 308 in 3.11. On 3.9/3.10 a permanent, method-preserving
    308 surfaced as `HTTPError: 308`, which check_one classified as `broken` — so the
    same URL was `broken` locally and `ok` in CI (which runs 3.12). Verdicts must not
    depend on the interpreter version. Example that triggered this:
    https://atelier-anchor.com/typefaces/smiley-sans/ -> 308 -> the same path sans slash.
    """

    def http_error_308(self, req, fp, code, msg, headers):
        return self.http_error_307(req, fp, code, msg, headers)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # 308 and 307 share semantics for our purposes: keep the method, follow Location.
        return super().redirect_request(req, fp, 307 if code == 308 else code,
                                        msg, headers, newurl)


def probe(url, method):
    """Return (status, final_url, body_text). body_text is '' for HEAD."""
    req = urllib.request.Request(url, method=method, headers={
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,*/*;q=0.8" if method == "GET" else "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    })
    ctx = ssl.create_default_context()
    handlers = [Redirect308, urllib.request.HTTPSHandler(context=ctx)]
    if NO_PROXY:
        # build_opener inherits the environment's proxies otherwise, including the
        # system-wide one macOS exposes through getproxies() with no env var set.
        handlers.insert(0, urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)
    with opener.open(req, timeout=TIMEOUT) as r:
        body = r.read(BODY_BYTES).decode("utf-8", "replace") if method == "GET" else ""
        return r.status, r.geturl(), body


def check_one(url, expect_js=False):
    """Return (status, detail). status in {ok, blocked, broken}.

    `expect_js` is the entry's `linkcheck_js` flag: the page is a known JavaScript app
    whose HTML carries no <title>/<h1>, so the JS-shell heuristic must not fire on it.

    GET is tried first (HEAD second) so the body is available for the checks below.
    Beyond the plain status code this now catches three things a status code hides:
      * soft 404 — a 200 whose own <title>/<h1> says "not found"  -> broken
      * JS shell — a 200 with no <title> and no <h1> at all        -> blocked
        (this is exactly how https://www.pretzel.rocks/library looked healthy while
         rendering h1 "Page not found" in a browser)
      * a redirect onto a different registrable domain             -> blocked
        (a rebrand such as freepik.com -> magnific.com must be reviewed by a human,
         not silently reported as ok)
    A redirect within the same domain is reported in the detail string instead.
    """
    orig = urlsplit(url)
    last = None
    pending = None  # a network-level `broken` waiting for confirmation on the other method
    for method in ("GET", "HEAD"):
        verdict = None
        network_broken = False
        for attempt in range(RETRIES + 1):
            try:
                code, final, body = probe(url, method)
            except urllib.error.HTTPError as e:
                last = "HTTP %d" % e.code
                if e.code in BLOCKED_CODES:
                    verdict = ("blocked", last)
                    break
                if e.code in (404, 410):
                    if method == "GET":
                        return ("broken", last)
                    verdict = None  # 404 on HEAD only: confirm with GET
                    break
                if 500 <= e.code < 600:
                    if attempt < RETRIES:
                        time.sleep(2 * (attempt + 1))
                        continue
                    # Retries exhausted. A 5xx reports the server's own state and proves
                    # nothing about whether the resource still exists, so it is
                    # indeterminate (`blocked`) rather than dead (`broken`).
                    verdict = ("blocked", "%s after %d attempts (server error, not a dead link)"
                               % (last, RETRIES + 1))
                    break
                return ("broken", last)
            except ssl.SSLError as e:
                last = "SSL: %s" % e
                verdict = ("blocked", last)
                break
            except OSError as e:
                msg = str(e)
                last = "%s: %s" % (type(e).__name__, msg)
                if "nodename nor servname" in msg or "Name or service not known" in msg:
                    return ("broken", "DNS failure")  # definitive, and cheap to re-resolve
                if attempt < RETRIES:
                    time.sleep(2 * (attempt + 1))
                    continue
                # Retries exhausted. A refusal normally means nothing is listening, but it can
                # also be one bad CDN edge: rocketstock.com refused urllib while `nc` reached
                # port 443 three times and curl got a clean 302 seconds earlier. So a refusal
                # is confirmed on the second method before it is allowed to fail CI.
                refused = "refused" in msg.lower()
                network_broken = refused
                verdict = (("broken", "connection refused after %d attempts" % (RETRIES + 1))
                           if refused else ("blocked", last))
                break

            # --- 2xx/3xx: look past the status code -----------------------------
            fin = urlsplit(final or url)
            if registrable(fin.netloc) != registrable(orig.netloc):
                return ("blocked", "%d but redirects to another domain: %s" % (code, final))
            if code in CONTENTLESS_OK:
                return ("blocked", "%d: accepted but no document returned (bot queue or empty "
                                   "response) — open in a browser" % code)
            if body:
                soft, text, had_marker = inspect_body(body)
                if soft:
                    return ("broken", "soft 404 (page says: %s)" % text)
                if not had_marker and not expect_js:
                    return ("blocked", "%d but HTML has no <title>/<h1> (JS shell?) — open in a browser" % code)
            moved = "" if (final or url).rstrip("/") == url.rstrip("/") else " -> %s" % final
            return ("ok", "%d%s" % (code, moved))
        if verdict:
            if network_broken:
                # Do not fail CI on one method's network error; let the other method confirm.
                pending = verdict
                continue
            if method == "GET":
                return verdict
            continue  # HEAD was the fallback and it is blocked too
    return pending or ("blocked", last or "unknown")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--slug", action="append", help="only check this category slug (repeatable)")
    ap.add_argument("--no-proxy", action="store_true",
                    help="ignore environment/system proxies, matching how CI runs")
    args = ap.parse_args()

    global NO_PROXY
    NO_PROXY = args.no_proxy
    proxies = urllib.request.getproxies()
    if NO_PROXY:
        print("Proxy: disabled (--no-proxy), matching CI.")
    elif proxies:
        print("Proxy: using %s — verdicts may differ from CI, which runs with no proxy. "
              "Re-run with --no-proxy to reproduce it." % (proxies.get("https") or proxies),
              file=sys.stderr)
    else:
        print("Proxy: none.")

    items = collect(args.slug)
    if not items:
        print("No URLs found.", file=sys.stderr)
        sys.exit(2)

    print("Checking %d URLs (%d workers)..." % (len(items), MAX_WORKERS))
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [(ex.submit(check_one, url, js), slug, name, url) for slug, name, url, js in items]
        for fut, slug, name, url in futures:
            try:
                status, detail = fut.result()
            except Exception as e:  # never crash the run
                status, detail = "blocked", "check crashed: %s" % e
            results.append((status, slug, name, url, detail))
            print("  [%-7s] %-42s %s" % (status, name[:42], detail), flush=True)

    ok = [r for r in results if r[0] == "ok"]
    blocked = [r for r in results if r[0] == "blocked"]
    broken = [r for r in results if r[0] == "broken"]

    print("\nSummary: %d ok, %d blocked (needs manual check), %d broken" % (len(ok), len(blocked), len(broken)))

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("## Link check: %d ok / %d blocked / %d broken\n\n" % (len(ok), len(blocked), len(broken)))
            for label, group in (("Broken (must fix)", broken), ("Blocked (manual check)", blocked)):
                if group:
                    f.write("### %s\n\n| Category | Resource | URL | Detail |\n| --- | --- | --- | --- |\n" % label)
                    for _, slug, name, url, detail in sorted(group, key=lambda r: (r[1], r[2])):
                        f.write("| %s | %s | %s | %s |\n" % (slug, name, url, detail))
                    f.write("\n")

    report_path = os.environ.get("LINKCHECK_REPORT")
    if report_path:
        lines = ["## Broken links (%d)" % len(broken), "",
                 "| Category | Resource | URL | Detail |",
                 "| --- | --- | --- | --- |"]
        for _, slug, name, url, detail in sorted(broken, key=lambda r: (r[1], r[2])):
            lines.append("| %s | %s | %s | %s |" % (slug, name, url, detail))
        lines += ["", "OK: %d \u00b7 Blocked (manual check): %d" % (len(ok), len(blocked))]
        with open(report_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")

    sys.exit(1 if broken else 0)


if __name__ == "__main__":
    main()
