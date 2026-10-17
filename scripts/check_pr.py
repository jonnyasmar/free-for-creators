#!/usr/bin/env python3
"""Mechanical contract check for a resource pull request.

`build_readme.py --validate-only` and `check_entry.py --all` judge the *data*: is this entry
well-formed, well-written, unique. This judges the *submission*, which is the part of
CONTRIBUTING that only a human used to enforce: that the PR changes one entry rather than
five, that its title and body describe that entry, that the file list is the file list a
resource submission produces, and that the checklist was actually completed rather than
pasted.

Runs on `pull_request_target` from the BASE checkout, so PR content is read through git
objects (`git show <ref>:<file>`) and never executed. Maintainers (OWNER/MEMBER/COLLABORATOR)
and bot accounts are exempt, because the weekly link-check commit, a category addition and a
CONTRIBUTING edit are all legitimate PRs that are not resource submissions.

Usage:
    python3 scripts/check_pr.py --event "$GITHUB_EVENT_PATH"
    python3 scripts/check_pr.py --event e.json --base-ref SHA --head-ref SHA   # local dry run

Env:
    PR_REPORT   Path to write the Markdown report the workflow comments on the PR

Exit codes: 0 = the submission follows the rules, 1 = it does not. License: CC0.
"""
import argparse
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from build_readme import url_key  # noqa: E402  (same normalisation the generator uses)
from pr_linkcheck import assert_head_is_pinned  # noqa: E402  (one ref-pinning rule, one place)

TRUSTED_ASSOCIATIONS = {"OWNER", "MEMBER", "COLLABORATOR"}

# What a resource submission is allowed to touch. README.md and README.zh-CN.md are the
# generated output, and assets/social-preview.html carries the two stamped counts — all
# three legitimately change when a contributor runs the documented commands. Everything
# else (workflows, scripts, CONTRIBUTING, the banner artwork) is a maintainer change.
ALLOWED_PATHS = ("README.md", "README.zh-CN.md", "assets/social-preview.html")
ALLOWED_PREFIXES = ("data/",)

TITLE_PATTERN = re.compile(r"^\[([^\[\]\r\n]+)\]\s*->\s*\[([^\[\]\r\n]+)\]$")
LINK_PATTERN = re.compile(r"^Link:\s*(https://\S+)\s*$", re.MULTILINE)
# The template ships the unanswered placeholder "Is this your product? Yes / No"; the
# lookahead is what stops "Yes" in it from counting as an answer.
OWNERSHIP_PATTERN = re.compile(
    r"^Is this your product\?\s*(Yes|No)\b(?!\s*/)(?:\s+\S.*?)?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
CHECKED_ITEM_PATTERN = re.compile(r"^\s*-\s*\[([xX ])\]\s*(.+?)\s*$", re.MULTILINE)

# Each required confirmation is matched by a fragment that survives reflowing and the
# removal of backticks, so reformatting the template box does not break the gate. The
# second element is the line as the template ships it, quoted back in the error because
# "which box did I miss?" is the only question this check should leave the author.
REQUIRED_CONFIRMATIONS = (
    ("data/*.json", "I edited `data/*.json` only — **not** `README.md` by hand"),
    ("build_readme.py", "I ran `python3 scripts/build_readme.py` and committed the regenerated README"),
    ("verified its current license", "I visited the resource and **verified its current license**"),
    ("not a duplicate", "The resource is legally free … , not a duplicate, and the URL has no tracking/affiliate parameters"),
    ("expiring credit", "The free tier is not a trial, an expiring credit, a recurring quota or a watermarked export"),
    ("values follow the semantics", "`attribution` / `monetization` / `signup` values follow the semantics in CONTRIBUTING.md"),
)


class ContractError(Exception):
    """A submission that cannot be judged at all (bad event payload, unfetchable ref)."""


def git(*args):
    result = subprocess.run(["git"] + list(args), capture_output=True, text=True)
    if result.returncode != 0:
        raise ContractError("git %s failed: %s" % (" ".join(args), result.stderr.strip()))
    return result.stdout


def set_output(key, value):
    """GitHub Actions step output, mirroring pr_linkcheck.py so the workflow reads the same."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("%s=%s\n" % (key, value))


def normalize_text(value):
    return " ".join(value.casefold().split())


def normalize_item(value):
    """Checklist items compared without backticks or whitespace runs."""
    return " ".join(value.replace("`", "").casefold().split())


def canonical(entry):
    return json.dumps(entry, sort_keys=True, ensure_ascii=False)


def categories_at(ref):
    """{filename: document} for data/*.json at <ref>, read as data and never executed."""
    docs = {}
    for name in git("ls-tree", "--name-only", ref, "data/").split():
        if not name.endswith(".json"):
            continue
        try:
            docs[name] = json.loads(git("show", "%s:%s" % (ref, name)))
        except ValueError:
            # A file that is not JSON at this ref is a CI problem, not a submission problem.
            continue
    return docs


def same_resource(a, b):
    """A correction keeps either the name or the URL — that is what makes it a correction."""
    return normalize_text(a.get("name", "")) == normalize_text(b.get("name", "")) or \
        url_key(a.get("url", "")) == url_key(b.get("url", ""))


def describe_changes(base_docs, head_docs):
    """Entry-level diff. A matched add+remove pair is one *correction*, not two changes."""
    changes = []
    for name in sorted(set(base_docs) | set(head_docs)):
        if name not in base_docs:
            changes.append({"kind": "category-added", "file": name, "entry": None})
            continue
        if name not in head_docs:
            changes.append({"kind": "category-removed", "file": name, "entry": None})
            continue
        before = {canonical(e): e for e in base_docs[name].get("entries", [])}
        after = {canonical(e): e for e in head_docs[name].get("entries", [])}
        added = [after[k] for k in after if k not in before]
        removed = [before[k] for k in before if k not in after]
        if len(added) == 1 and len(removed) == 1 and same_resource(added[0], removed[0]):
            changes.append({"kind": "corrected", "file": name, "entry": added[0]})
            continue
        for entry in added:
            changes.append({"kind": "added", "file": name, "entry": entry})
        for entry in removed:
            changes.append({"kind": "removed", "file": name, "entry": entry})
    return changes


def describe(change):
    entry = change["entry"]
    if entry is None:
        return "%s %s" % (change["kind"], change["file"])
    return "%s %s (%s)" % (change["kind"], entry.get("name", "?"), change["file"])


def unchecked_and_checked(body):
    checked, unchecked = [], []
    for mark, text in CHECKED_ITEM_PATTERN.findall(body or ""):
        (checked if mark.lower() == "x" else unchecked).append(text)
    return checked, unchecked


def check_confirmations(body, errors):
    checked, _ = unchecked_and_checked(body)
    present = [normalize_item(item) for item in checked]
    for fragment, template_line in REQUIRED_CONFIRMATIONS:
        if not any(fragment in item for item in present):
            errors.append(
                "The checklist item was not ticked: %s\n"
                "     (copy the line from .github/PULL_REQUEST_TEMPLATE.md and tick it — "
                "unchecked and reworded boxes both fail this, and a maintainer is exempt.)"
                % template_line
            )


def check_scope(changed_files, errors):
    for path in changed_files:
        if path in ALLOWED_PATHS or path.startswith(ALLOWED_PREFIXES):
            continue
        errors.append(
            "%s is outside the file list a resource submission may change "
            "(allowed: data/**, %s). If that is deliberate, a maintainer can merge it — "
            "they are exempt from this check." % (path, ", ".join(ALLOWED_PATHS))
        )


def check_single_change(changes, errors):
    if not changes:
        errors.append(
            "This PR does not add, correct or remove an entry. A resource submission must "
            "change one entry; a metadata or wording change is a maintainer PR."
        )
        return None
    if len(changes) > 1:
        errors.append(
            "A resource submission changes exactly one entry; this PR changes %d:\n%s\n"
            "     Split it, so each entry can be reviewed and reverted on its own."
            % (len(changes), "\n".join("       - " + describe(c) for c in changes))
        )
        return None
    return changes[0]


def check_proposal(title, body, change, base_docs, errors):
    entry = change["entry"] or {}
    title_match = TITLE_PATTERN.match((title or "").strip())
    if not title_match:
        errors.append(
            "The PR title must be [Resource name] -> [Category], e.g. "
            "[Poly Haven] -> [3D & Game Assets] (got %r)." % (title or "")
        )
    else:
        given_name, given_category = title_match.group(1), title_match.group(2)
        if entry and normalize_text(given_name) != normalize_text(entry.get("name", "")):
            errors.append(
                "The PR title names %r but the entry is %r — they must match."
                % (given_name, entry.get("name", ""))
            )
        category = (base_docs.get(change["file"]) or {}).get("category") \
            or (base_docs.get(change["file"]) or {}).get("slug", "")
        if category and normalize_text(given_category) != normalize_text(category):
            errors.append(
                "The PR title says category %r but %s holds %r."
                % (given_category, change["file"], category)
            )

    link_match = LINK_PATTERN.search(body or "")
    if not link_match:
        errors.append(
            "Add the resource URL to the PR body on its own line, as "
            "`Link: https://example.com/`."
        )
    elif entry and url_key(link_match.group(1)) != url_key(entry.get("url", "")):
        errors.append(
            "The `Link:` in the PR body (%s) is not the entry's URL (%s)."
            % (link_match.group(1), entry.get("url", ""))
        )

    if not OWNERSHIP_PATTERN.search(body or ""):
        errors.append(
            "Answer `Is this your product? Yes / No` in the PR body. The placeholder must "
            "be replaced — self-submissions are welcome and credited, but they have to be "
            "disclosed."
        )

    check_confirmations(body, errors)


def validate(base_ref, head_ref, pull_request, changed_files=None, write_report=True):
    """Return (errors, warnings). Never raises for a bad submission, only for bad input."""
    errors, warnings = [], []
    if changed_files is None:
        changed_files = [p for p in git("diff", "--name-only", base_ref, head_ref).splitlines() if p]
    if not changed_files:
        errors.append("This PR does not change any file.")
        return finish(errors, warnings, None, write_report)

    check_scope(changed_files, errors)
    base_docs = categories_at(base_ref)
    head_docs = categories_at(head_ref)
    changes = describe_changes(base_docs, head_docs)
    change = check_single_change(changes, errors)

    if change and change["kind"] in ("category-added", "category-removed"):
        errors.append(
            "%s: adding or removing a category means editing CATEGORY_ORDER and the site's "
            "CAT_COLORS as well, so it is a maintainer PR." % describe(change)
        )
        change = None

    if change:
        check_proposal(pull_request.get("title", ""), pull_request.get("body") or "",
                       change, base_docs, errors)
    return finish(errors, warnings, change, write_report)


def finish(errors, warnings, change, write_report):
    if write_report:
        write_report_file(errors, warnings, change)
    return errors, warnings


def write_report_file(errors, warnings, change):
    path = os.environ.get("PR_REPORT")
    if not path:
        return
    if errors:
        lines = ["**This PR does not follow the submission contract yet.**", ""]
        lines += ["- %s" % e.replace("\n", "\n  ") for e in errors]
        lines += ["", "The rules are in [CONTRIBUTING.md](../blob/main/CONTRIBUTING.md); "
                      "the template is [.github/PULL_REQUEST_TEMPLATE.md](PULL_REQUEST_TEMPLATE.md). "
                      "Maintainers are exempt from this check."]
    else:
        what = describe(change) if change else "a maintainer change"
        lines = ["✅ **PR contract check passed** — %s, title and body match, checklist complete." % what,
                 "",
                 "A maintainer must still verify that the resource is genuinely free and "
                 "that the licence in the entry is the one the site states."]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def load_event(path):
    with open(path, encoding="utf-8") as fh:
        event = json.load(fh)
    if "pull_request" not in event:
        raise ContractError("%s is not a pull_request event payload" % path)
    return event


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--event", required=True, help="path to the GitHub event payload")
    ap.add_argument("--base-ref", help="override the base commit (local dry run)")
    ap.add_argument("--head-ref", help="override the head ref (local dry run)")
    ap.add_argument("--force", action="store_true",
                    help="run the checks even when the author is exempt (used by the tests)")
    args = ap.parse_args()

    try:
        event = load_event(args.event)
    except (OSError, ValueError, ContractError) as exc:
        print("Contract check could not read the event: %s" % exc, file=sys.stderr)
        return 1

    pull_request = event["pull_request"]
    base_ref = args.base_ref or pull_request["base"]["sha"]
    head_ref = args.head_ref or "FETCH_HEAD"
    expected_head = pull_request["head"]["sha"]
    try:
        if args.head_ref:
            # --head-ref is the operator's local escape hatch (it takes any ref name, not a
            # SHA), so the pin — which exists to close the force-push window in CI — does not
            # apply. CI never passes it.
            print("Local dry run: head ref is %s (not pinned to the event's SHA)." % args.head_ref)
        else:
            assert_head_is_pinned(head_ref, expected=expected_head)
    except RuntimeError as exc:
        print("Contract check could not resolve the PR ref: %s" % exc, file=sys.stderr)
        return 1

    # Refuse to be clever about a stale base: the event's base commit is the contract.
    if not args.base_ref:
        try:
            git("cat-file", "-e", base_ref + "^{commit}")
        except ContractError:
            git("fetch", "--no-tags", "origin", base_ref)

    login = (pull_request.get("user") or {}).get("login", "")
    association = pull_request.get("author_association", "NONE")
    exempt = association in TRUSTED_ASSOCIATIONS or login.endswith("[bot]")
    if exempt and not args.force:
        print("%s is %s — contract checks are skipped (maintainer/bot PR)." % (login or "author", association))
        set_output("skip", "true")
        return 0
    set_output("skip", "false")

    errors, warnings = validate(base_ref, head_ref, pull_request)

    for warning in warnings:
        print("  warn  %s" % warning)
    for error in errors:
        print("  ERROR %s" % error.replace("\n", "\n         "))
    if errors:
        print("\nContract check FAILED with %d problem(s)." % len(errors))
        return 1
    print("Contract check passed: one entry, title/body match, checklist complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
