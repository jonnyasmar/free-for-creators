# Listing submissions — a kit, not a to-do list

Where the directory is already listed, where it is worth listing, and the exact line to paste.
Everything here was checked against the target repository's own README at the time of writing;
sections move, so re-read the target's header before you open the pull request.

**Disclose authorship.** You are the maintainer of this list, and more than one of these
repositories now asks directly ("Is this your product?"). Say so in the first line — a disclosed
self-submission is normal; a hidden one gets closed.

---

## Already listed

| Repository | Where | Note |
| --- | --- | --- |
| [bradtraversy/design-resources-for-developers](https://github.com/bradtraversy/design-resources-for-developers) | `## Others` | Added already. `Others` is that README's catch-all; a move into a topical section would need a maintainer's call, not a PR from here |

## Worth submitting

### 1. neutraltone/awesome-stock-resources → `## Aggregated Content`

Their section definition is "a liberal mixture of content aggregated from other free resources
and made available at one central point" — which is exactly what this is. The neighbouring
entries are `All The Free Stock`, `Libre Stock`, `Stock Up`, `The Stocks`.

Paste at the end of the `## Aggregated Content` list (keep their alphabetical-ish order):

```markdown
* [Free for Creators](https://github.com/skyzhao1223/free-for-creators) - A license-transparent directory of free creator assets: every entry records the license, whether attribution is required, whether it is safe in monetized content, and whether an account is needed. CC0 data.
```

- Repository: <https://github.com/neutraltone/awesome-stock-resources>
- Read their [CONTRIBUTING](https://github.com/neutraltone/awesome-stock-resources/blob/master/CONTRIBUTING.md) first; the list is curated by hand and rejects entries it cannot verify.
- Suggested PR title: `Add Free for Creators to Aggregated Content`

### 2. Kavex/GameDev-Resources

Sections are asset-type based (`2D Assets`, `3D Assets`, `Audio Assets`,
`Multiple Graphic Collections`, `Blogs and Portals`). This directory is a cross-asset index
rather than a single asset pack, so **check the current section list on the day** and pick the
closest index-style slot; if none fits, that is a signal not to submit rather than to force it
into `Blogs and Portals`.

- Repository: <https://github.com/Kavex/GameDev-Resources>

### 3. ripienaar/free-for-dev → probably **not**

They index services that offer a free tier for developers (`## Design and UI`,
`## Storage and Media Processing`). A directory of third-party assets is not a service with a
free tier, so it does not belong in either section. Listed here so the decision is recorded
rather than re-litigated: **skip unless their contributing guide says otherwise.**

## Not applicable

- **public-apis** — the Related Lists entry is about APIs; this is not one. It does expose
  `site/data/all.json`, but that is a dataset, not an API with terms.
- **Sister lists in our own README** — `free-for-dev`, `awesome-stock-resources`,
  `GameDev-Resources` and `design-resources-for-developers` are already linked from
  [README.md](README.md#related-lists); that link is inbound, not a submission.

## Before opening any of these

1. Re-read the target's `CONTRIBUTING.md` and README header — sections are renamed often.
2. Check the directory is not already there (search the raw README for `free-for-creators`
   and for the repository URL).
3. Use the one-line format the target uses, and match its punctuation: some lists want a
   trailing period, some do not, and several require a licence marker.
4. Disclose that you maintain it, in the first line of the PR body.
5. Do not paste our README's columns in. Their format is `* [Name](url) - sentence.`; the
   licence-transparency argument belongs in the PR body, not inside the list item.
