# Asset Factory

A reusable workflow that validates a Site Variable Sheet, audits a WordPress
clone, applies only approved values, and reports everything it refused to do.

The design principle is narrow and deliberate: **the automation applies what it
can defend and escalates what it cannot.** It has no code path that invents a
business fact, resolves a conflict, or infers a missing value from context.

---

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv && . .venv/Scripts/activate   # Windows
pip install -r requirements.txt
cp .env.example .env
```

Fill in `.env`:

```
WP_BASE_URL=https://your-clone.cloudwaysapps.com
WP_USER=your-wp-username
WP_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx
```

`WP_APP_PASSWORD` is a **WordPress Application Password**
(Users → Profile → Application Passwords), not an account login password.
`.env` is gitignored and must never be committed, logged, recorded or submitted.

Place the Site Variable Sheet at `inputs/site-variable-sheet.xlsx`, or point
`source.variable_sheet` in `config/site.yml` somewhere else.

## Usage

Either `python asset-factory.py <command>` (no PYTHONPATH needed) or
`python -m asset_factory.cli <command>` with `src/` on the path.

```bash
python asset-factory.py validate       # parse and validate the sheet only
python asset-factory.py audit          # read-only audit of the target site
python asset-factory.py plan           # dry run - proposes changes, writes nothing
python asset-factory.py apply --confirm                # titles, SEO fields, nav
python -m asset_factory.cli content                    # dry run content fill
python -m asset_factory.cli content --confirm          # place approved content
python -m asset_factory.cli images --phase 1           # dry run image pass
python -m asset_factory.cli images --phase 1 --confirm # optimise, upload, place
python asset-factory.py seo                            # Rank Math on-page audit
python asset-factory.py links                          # dry run internal links
python asset-factory.py links --confirm                # apply approved link rules
python -m asset_factory.cli prune                      # dry run removals
python -m asset_factory.cli prune --confirm            # remove empty card slots
python -m asset_factory.cli evidence                   # before/after report
python -m asset_factory.cli rollback <snapshot.json> --confirm
```

Recommended order on a fresh clone: `validate` → `plan` → `apply --confirm` →
`content --confirm` → `images --phase 1 --confirm` → `links --confirm` →
`evidence`.

**Purge the Breeze page cache after any run.** Breeze exposes no REST route,
so this is manual: WP Admin toolbar → Breeze → Purge All Cache. Skipping it
makes an audit report resolved placeholders as still unresolved — during this
build, cached HTML showed 83 remaining placeholders where the true figure was
53.

`plan` is the default working mode and requires no credentials. There is no
flag that turns `plan` into a write; `apply` is a separate command and refuses
to run without `--confirm`.

Outputs land in `out/`:

| File | Contents |
|---|---|
| `dry-run.md` | Proposed changes with before/after and the source variable |
| `exception-report.md` | Everything refused, grouped by reason, with required action |
| `exception-report.csv` | The same, for spreadsheet triage |
| `plan.json` | Machine-readable plan including change fingerprints |
| `variables.json` | Full validation result for every sheet row |
| `post-apply-audit.json` | Verification pass written after `apply` |
| `*.html` / `*.pdf` | The same reports rendered for a browser and for print |

The HTML and PDF versions are transforms of the Markdown the emitters already
produced, printed by headless Chrome so the PDF and the web version are
rendered by one engine and cannot disagree about the same report.

## Configuration

All site-specific data lives in `config/site.yml` and `.env`. The code in
`src/` contains no site name, URL, page ID, or field value. Running against a
different clean clone means editing the config, not the automation.

`config/site.yml` declares:

- `target` — base URL and credentials, both by `${ENV_VAR}` reference
- `globals` — site-wide components and the variable each depends on
- `menus` — navigation items matched by their current label
- `pages` — each page matched by slug, with a field → variable mapping
- `policy` — fields the workflow must never write, each with a stated reason

## How it decides

The Status column of the variable sheet is authoritative. Validation can only
ever **demote** a field's usability, never promote it — there is no `promote()`
in the codebase, and a test asserts that.

Four things block a write:

| Condition | Handling |
|---|---|
| Status is not `Approved` | Never written, whatever the cell contains |
| Status is `Approved` but the cell is empty | Treated as missing |
| Value fails a **type** check (phone, email, URL, domain) | Demoted and reported |
| Variable is absent from the sheet entirely | Reported against the config key |

One thing does *not* block a write. A value that is `Approved` and correctly
typed but has a **quality** concern — an SEO title past 60 characters, say — is
still applied, with the concern recorded as an advisory. Declining to apply an
approved value would be inventing a decision the sheet did not delegate. That
is the same failure as inventing a value, in the opposite direction.

### The phantom-value case

`{{PHONE_NUMBER}}` in the supplied sheet holds `0.0` with status `Missing`. The
cell is non-empty and truthy; a spreadsheet coerced an empty numeric cell. Any
implementation keyed on value presence rather than status publishes `0.0` as
the business phone number across the header, footer, and every CTA.

This workflow rejects it twice over: the status gate catches it first, and the
phone validator rejects any purely numeric value with a message that names the
real cause. `tests/test_sheet.py` locks both behaviours in.

## Known limitations and manual steps

Stated plainly, because a limitation presented as automation is worse than a
limitation.

- **12 placeholders remain, each for a stated reason.** 283 at the start, 271
  resolved (96%). What is left, and why:
  - All **12** are `{{ADDRESS}}`, `{{PHONE_NUMBER}}` and `{{EMAIL}}` in the
    global footer, the same three fields rendered on four pages. None is
    supplied or approved.
- **Those three are deliberately NOT pruned.** They are contact fields
  awaiting client values, not surplus template slots: delete them and the real
  phone number has nowhere to go when it arrives. They are also the clearest
  live evidence that the workflow refuses unapproved data.
- **Surplus card slots WERE pruned** (`prune --confirm`, opt-in). Both
  templates repeat a service group twice — 17 card slots each — while the
  package supplies one list of 9 services. The surplus can only be filled by
  inventing service names and descriptions. Removing an empty repeater
  instance invents nothing and is reversible from `snapshots/prune-*.json`.
  This removes *instances* of a repeated card, not the pattern, which remains
  in the site's pattern library untouched.
- **15 images placed across 34 slots, every one with alt text**, declared in
  `config/images.yml` and verified from rendered HTML rather than from the
  write response. Source 16.2 MB -> 1.9 MB WebP (88% smaller), each file under
  a 250 KB budget via an automatic quality ladder.
- **Section images could only be placed after the content pass.** Outside the
  hero, no slot carries a semantic class, so relevance is judged against the
  heading that follows the slot in the block tree — which does not exist until
  the content is in. `images --phase 1` places heroes; `--phase 2` the rest.
- **10 placements are marked `general`, not `specific`,** in
  `out/image-placement-log.json`. Spider, Flea and Mosquito have no
  pest-specific photograph in the set, so those cards carry topically relevant
  imagery and the imprecision is disclosed rather than presented as a match.
- **One image slot is deliberately left unfilled.** The Header element's
  `Overlay Logo` slot still points at the GeneratePress placeholder. It is a
  *logo* slot, no approved logo asset is supplied, and a content photograph in
  a logo slot is worse than an unresolved one.
- **Slugs are audited, never rewritten.** Only `HOMEPAGE_SLUG` is approved.
  `service1_name` and `location-page-template` remain as found and are reported.
- **`noindex` is deliberately left in place** on all four pages. This is a
  staging clone and the brief prohibits publishing it publicly, so `noindex`
  is the correct state. Clearing it to satisfy an SEO checker would expose a
  staging site.
- **Rank Math fields are not writable through the standard WP REST meta API.**
  `GET /wp/v2/pages/<id>` returns `meta: {"footnotes": ...}` and nothing else;
  posting `meta: {"rank_math_title": ...}` returns HTTP 200 and writes nothing.
  This workflow writes through Rank Math's own namespace,
  `POST /wp-json/rankmath/v1/updateMeta`, and `apply` runs a pre-flight that
  fails loudly if that namespace is absent.
- **Menu item updates require authentication** and are matched by current
  label; renaming an item by hand between runs will cause it to be skipped
  rather than mismatched.

## Repeatability

`apply` compares each field's current value against the proposed value and
skips anything already correct, so a second run is a no-op rather than a
duplicate. After writing, it re-reads the site and re-plans; any change that
did not take effect is reported as a failure rather than assumed successful.

## Rollback

`apply` writes `snapshots/<timestamp>.json` **before** the first write,
capturing the site title and every in-scope page's title and Rank Math fields.

```bash
python -m asset_factory.cli rollback 20260825T081000Z.json          # preview
python -m asset_factory.cli rollback 20260825T081000Z.json --confirm
```

Without `--confirm` it prints what it would restore and exits. WordPress post
revisions provide a second, independent recovery path for page content.

## Tests
```bash
python -m pytest tests/ -q
```

18 tests. They encode the specific ways this workflow could silently publish
wrong data — the `0.0` phone, the conflicting CTA that looks corroborated by
two adjacent approved values, the missing `MAIN_CITY` that looks derivable from
approved SEO titles — plus the opposite failure of refusing approved work.

## AI tools used

- **Claude Code (Opus)** — used throughout for implementation, with the
  architecture, validation policy, and every judgment call in "How it decides"
  directed by the candidate. All output reviewed line by line.
- **Google Gemini 2.5 Flash Image** — used to generate site photography, if
  images are submitted. All generated imagery is free of text, logos, branded
  vehicles, license plates, and identifiable faces, so that no image asserts a
  business fact that the variable sheet does not supply.

## External services used

- **Google Gemini** (image generation) — no RNRF credentials or materials
  transmitted; prompts describe generic Southern California residential
  property, not client data.
- **Royal MCP** (WordPress plugin, pre-installed on the clone) — enabled during
  this assessment to provide REST tooling for reconnaissance and verification.
  Disclosed for approval; happy to disable it. The automation itself does not
  depend on it and runs against the standard WordPress and Rank Math REST APIs.

No RNRF credentials are transmitted to or retained by any external service.
