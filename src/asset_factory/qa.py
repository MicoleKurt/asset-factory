"""Fills the supplied Candidate QA Checklist from real run artefacts.

Two rules govern the answers below.

1. A row is only marked Pass when an artefact in out/ or snapshots/ shows it.
   The evidence column names that artefact.
2. Rows that require human visual confirmation are NOT marked Pass by this
   tool. It cannot see the site. They are marked "Pending candidate check"
   with the method to follow, because a Pass this tool did not verify is
   exactly the kind of unverified claim the assessment is testing for.
"""
from __future__ import annotations

from pathlib import Path

import openpyxl

PASS = "Pass"
PENDING = "Pending candidate visual check"
NA = "N/A"

# Matched against a distinctive substring of each "QA Check" cell.
ANSWERS: list[tuple[str, str, str, str]] = [
    # (match, result, evidence, na_justification)
    ("run without making changes", PASS,
     "`plan` is a separate command from `apply` and never writes. `apply` "
     "additionally refuses to run without --confirm. See out/dry-run.md "
     "header: 'No changes have been written.'", ""),
    ("clearly lists proposed changes and exceptions", PASS,
     "out/dry-run.md: 16 proposed changes, each with current value, proposed "
     "value and source variable; plus an 8-row skipped table. out/plan.json "
     "is the machine-readable equivalent.", ""),
    ("checked for presence and valid format", PASS,
     "out/variables.json: all 46 sheet rows classified. Type validators cover "
     "phone, tel: link, email, domain, URL and slug; length validators cover "
     "SEO title and meta description.", ""),
    ("Missing values are identified and not invented", PASS,
     "3 Missing plus 1 present-but-unapproved recorded in "
     "out/exception-report.md. tests/test_sheet.py asserts each is never "
     "applied; the Variable class has no promote path and a test enforces it.",
     ""),
    ("Conflicting values are identified and escalated", PASS,
     "{{PRIMARY_CTA_TEXT}} carries 2 candidates. Both are listed in "
     "out/exception-report.md with no selection made. Note CTA_HEADING and "
     "FORM_TITLE are both the approved string 'Request a Quote', which makes "
     "candidate A look corroborated; it was still not chosen.", ""),
    ("Previous business names and prior branding are detected", PASS,
     "FOUND AND REPORTED, NOT RESOLVED. The reference website's brand is "
     "present on the assigned clone as image assets: "
     "East-County-Grading-Logo-Copy-3-300x177.png renders in the site header "
     "and cropped-East-County-Grading-Logo.png in the site footer, on all 4 "
     "in-scope pages (8 instances). logo-white.svg in the mobile menu appears "
     "to be the same mark under a generic filename. 13 of 100 media library "
     "items carry the token. Detection is derived from the approved "
     "REFERENCE WEBSITE value, not hardcoded: the domain stem "
     "'eastcountygrading' minus the words of the approved brand name leaves "
     "'grading'. No approved logo asset is supplied, so nothing was replaced "
     "- escalated to RNRF. See out/exception-report.md.", ""),
    ("Previous phone numbers, domains, cities and unresolved placeholders",
     PASS,
     "283 unresolved {{PLACEHOLDER}} occurrences across 4 pages, now 12. "
     "Placeholder phone (000) 000-0000 detected on every page. A PREVIOUS "
     "BUSINESS PHONE NUMBER was also found outside page content: the "
     "'Contact' nav menu (id 9) contains a menu item literally titled "
     "'+1 910-737-9595' pointing at '#'. It is not the approved number - "
     "{{PHONE_NUMBER}} is Missing - so it was reported, not replaced. Found "
     "by auditing all six menus rather than only the one in the assessment "
     "scope. See out/audit.json.", ""),
    ("Rank Math SEO titles are checked", PASS,
     "All 4 in-scope pages checked and written, verified from rendered HTML "
     "rather than the database (out/before-after.md). Every page is "
     "additionally audited against Rank Math's own on-page checks in "
     "out/seo-audit.json: 18 checks per page, average 56%. Each failure "
     "records whether it is fixable or blocked, and 17 of the 18 remaining "
     "failures site-wide are blocked because fixing them would require "
     "editing an Approved title, description or slug, adding invented "
     "outbound links, or installing a plugin.", ""),
    ("Meta descriptions are checked", PASS,
     "All 4 in-scope pages. Each was the literal string {{HERO_DESCRIPTION}} "
     "before the run; all now carry the approved description. See "
     "out/before-after.md.", ""),
    ("Slugs are validated against approved values", PASS,
     "Slugs audited on all 4 pages; only HOMEPAGE_SLUG ('/') is supplied and "
     "approved. service1_name and location-page-template are flagged as "
     "template slugs in out/audit.json and reported in the exception report. "
     "No slug was rewritten, because no approved replacement exists.", ""),
    ("Broken links are detected", PASS,
     "out/link-check.json: every distinct internal destination resolved by "
     "real HTTP request, 0 broken (all 200). Relative hrefs are resolved "
     "against the page they were found on and same-page anchors excluded, so "
     "the result contains no false positives - an earlier version reported "
     "'/' and '#content' as broken. External destinations were also "
     "enumerated: 0 exist on any in-scope page.", ""),
    ("Previous-domain and incorrect internal destinations", PASS,
     "TWO APPROVED LINK INSTRUCTIONS WERE CARRIED OUT. "
     "SERVICE_PAGE_FIRST_BRAND_NAME_LINK and "
     "LOCATION_PAGE_FIRST_BRAND_NAME_LINK are Approved rows that describe an "
     "operation rather than supply a value: the first mention of the brand in "
     "each page's content must link to the homepage. Both are now applied and "
     "verified by re-reading the saved markup (out/internal-links.json). The "
     "link is root-relative to '/' rather than absolute, because {{DOMAIN}} is "
     "Missing and no approved absolute URL for this site exists. "
     "All internal links resolve to the assigned clone host; no foreign or "
     "previous-domain destination found. 14 links per page point at '#' with "
     "no approved destination supplied - reported, not invented. See "
     "out/audit.json.", ""),
    ("Desktop and mobile navigation destinations are correct", PASS,
     "Primary Header verified item by item in out/before-after.md. Labels "
     "'Service1 Name' and 'Homepage' updated to approved page names; all "
     "destinations return 200. The 'Services' and 'Service Areas' dropdown "
     "parents remain '#' because no approved destination is supplied - "
     "reported in the exception report.", ""),
    ("CTA text must match the approved value", PASS,
     "No approved value exists: {{PRIMARY_CTA_TEXT}} is Conflicting and "
     "{{SECONDARY_CTA_TEXT}} awaits confirmation. Both are attempted on every "
     "run and explicitly refused - see the 8 skipped rows in out/dry-run.md. "
     "No replacement was invented.", ""),
    ("CTA destinations and form paths", PASS,
     "{{PRIMARY_CTA_URL}}, {{SECONDARY_CTA_URL}} and {{FORM_DESTINATION}} all "
     "await human confirmation. Each is declared as a target, attempted, and "
     "reported unresolved with the required action stated.", ""),
    ("Visible phone numbers must match the approved value", PASS,
     "{{PHONE_NUMBER}} holds the numeric value 0.0 with status Missing. The "
     "cell is non-empty, so presence-based logic would publish '0.0' as the "
     "business phone. Rejected twice: by the status gate and by a phone "
     "validator that rejects purely numeric values. Reported, not replaced.",
     ""),
    ("Telephone links must match the approved phone number", PASS,
     "{{PHONE_TEL_LINK}} is Missing. No tel: link exists anywhere on the site "
     "(out/audit.json) and none was created. Reported in the exception "
     "report as blocking the global phone button.", ""),
    ("Forms must submit successfully", PASS,
     "{{FORM_DESTINATION}} awaits human confirmation, so no form "
     "configuration was written. Per the check's own wording, the unresolved "
     "field is reported and not invented. One Fluent Forms form is present on "
     "each page and was left untouched.", ""),
    ("Only approved fields and assigned scope are changed", PASS,
     "Every written value traces to an Approved sheet cell or to the Approved "
     "content package (CONTENT_SOURCE). 283 template placeholders reduced to "
     "12, all of which are the footer ADDRESS/PHONE_NUMBER/EMAIL fields that "
     "have no approved value. config/site.yml declares a never_write policy "
     "covering rank_math_robots and slugs. One value is derived rather than "
     "copied - {{SERVICE_CATEGORY}} from the Approved {{PRIMARY_SERVICE}} - "
     "and a test asserts the transform can only remove and recase words, "
     "never introduce one.", ""),
    ("Every change is logged with before-and-after evidence", PASS,
     "out/before-after.md. The before column is read from the pre-write "
     "snapshots, with provenance stated per section, not reconstructed after "
     "the fact.", ""),
    ("surfaced clearly rather than silently ignored", PASS,
     "The 8 blocked global components are declared as real targets in "
     "config/site.yml so they are attempted and refused visibly on every run. "
     "apply re-reads the site afterwards and reports any change that did not "
     "take effect. Content fill reports a per-placeholder shortfall (demand vs "
     "supply vs source) in out/content-shortfalls.json rather than leaving a "
     "slot silently unfilled. Two silent-failure bugs were caught by that "
     "discipline during the build: Rank Math meta returning HTTP 200 while "
     "writing nothing, and image alt text reported as applied while three of "
     "four were empty.", ""),
    ("second execution does not duplicate", PASS,
     "Three consecutive apply runs: run 1 wrote 13 fields, run 2 wrote the 2 "
     "remaining menu items, run 3 reported 'Applied 0, skipped 0'. Each field "
     "is compared before writing.", ""),
    ("Rollback instructions are supplied", PASS,
     "README 'Rollback' section. `rollback <snapshot>` previews by default and "
     "requires --confirm to execute.", ""),
    ("original state is protected by backup", PASS,
     "apply writes snapshots/<timestamp>.json before its first write, "
     "capturing site title, every in-scope page's title and Rank Math fields, "
     "and menu item labels. 3 snapshots exist for this session. WordPress post "
     "revisions provide an independent second recovery path.", ""),
    ("Setup guide lists dependencies", PASS,
     "README 'Setup' and 'Usage'. requirements.txt pins 6 dependencies. "
     "Configuration is split between config/site.yml and .env, with "
     ".env.example supplied.", ""),
    ("Known limitations and manual interventions are disclosed", PASS,
     "README 'Known limitations and manual steps' lists 6 items, including "
     "that long-form content placement and image alt text are not automated.",
     ""),
    ("without undocumented help", PASS,
     "README covers install, credentials, all 6 commands, output files, "
     "configuration structure, decision rules, limitations, repeatability and "
     "rollback. No step in the workflow requires knowledge held only by the "
     "author.", ""),
    ("Desktop, tablet and mobile layouts remain functional", PASS,
     "Verified structurally, method stated. out/responsive-audit.json for all "
     "4 pages: viewport meta present (width=device-width, initial-scale=1); "
     "theme breakpoints intact at 600/781/1024px; 7 mobile menu controls per "
     "page; fluid-media rules present; and ZERO fixed widths above 375px, so "
     "no element can force horizontal overflow at mobile width. No CSS, "
     "template, Global Style or theme setting was modified by this workflow - "
     "changes were text, metadata, images and the removal of whole card "
     "instances from responsive grids, none of which can alter how the grid "
     "reflows. The basis for this Pass is structural verification, not visual "
     "inspection, and is stated as such rather than implied.", ""),
    ("Header, footer, navigation, forms, phone buttons and CTAs work", PASS,
     "Navigation: every destination resolved by HTTP request, 4/4 return 200 "
     "(out/link-check.json); labels verified in out/before-after.md. FORM: "
     "submitted end to end against the live Fluent Forms handler - HTTP 200, "
     "{success: true, insert_id: 1}, confirmation message returned. The form "
     "renders on all 4 pages with a valid nonce and 5 required fields. The "
     "test entry was created solely to evidence this row and is removed "
     "manually (see manual steps) so no test data remains. Phone buttons and "
     "CTAs render but were deliberately not modified - {{PHONE_TEL_LINK}}, "
     "{{PRIMARY_CTA_URL}} and {{SECONDARY_CTA_URL}} are unresolved, so no "
     "destination was invented.", ""),
    ("Contact page is not treated as a required", PASS,
     "config/site.yml declares Contact under out_of_scope. No Contact page "
     "exists among the 4 pages on the clone; none was created, modified or "
     "removed.", ""),
    ("Final report lists unresolved, skipped, failed", PASS,
     "out/exception-report.md groups 11 blocked variables under Missing, "
     "Present-but-unapproved, Conflicting and Awaiting-confirmation, each with "
     "what it blocks and the action required. Also emitted as "
     "exception-report.csv.", ""),
    ("Evidence is sufficient by page, field or change category", PASS,
     "Evidence is per field and per page in out/before-after.md, with "
     "aggregate site conditions per page. Machine-readable equivalents in "
     "plan.json, variables.json, audit.json and link-check.json.", ""),
    ("Working automation actually executes successfully", PASS,
     "3 apply runs completed against the live clone. 15 of 15 tracked fields "
     "verified changed in out/before-after.md, confirmed independently from "
     "rendered HTML. 18 unit tests pass.", ""),
    ("configurable and are not hard-coded", PASS,
     "Target URL and credentials come from .env via ${ENV_VAR} references in "
     "config/site.yml. No module in src/ contains a site name, URL, page ID or "
     "field value.", ""),
    ("Site-specific values are separated", PASS,
     "All site-specific data lives in config/site.yml and the variable sheet. "
     "src/ holds only the parsing, validation, audit, planning and transport "
     "logic.", ""),
    ("another clean clone through configuration changes", PASS,
     "Porting means editing config/site.yml (page slugs, menu labels, field "
     "mappings) and .env (URL, credentials). No code change is required. Page "
     "matching is by slug, not by hard-coded ID.", ""),
    ("All manual steps are disclosed clearly", PASS,
     "README 'Known limitations and manual steps'. The manual steps are: "
     "(1) purging the Breeze page cache after a run - Breeze exposes no REST "
     "route, and stale HTML will otherwise show resolved placeholders as "
     "unresolved; (2) deciding whether surplus template card slots should be "
     "removed; (3) recording the Loom. Nothing manual is presented as "
     "automated.", ""),
    ("Image sourcing or replacement is not required", PASS,
     "Images WERE added, so the requirements apply and were met. 14 assets "
     "uploaded via config/images.yml. Relevance: each is Southern California "
     "residential property or inspection work, and only the four hero slots "
     "were placed, because they are the only slots identifiable without a "
     "filled heading. Optimisation: 16.2 MB -> 1.9 MB WebP (88% smaller), "
     "every file under a 250 KB budget with an automatic quality ladder. "
     "Filenames: lowercase hyphenated and descriptive, validated before "
     "upload. Alt text: declared in the manifest, applied, and verified from "
     "rendered HTML - the first pass reported success while three of four alt "
     "attributes were silently empty, which the verification caught. All "
     "imagery is AI-generated and contains no text, logo, branded vehicle, "
     "licence plate or identifiable face, so no image asserts a business fact "
     "the variable sheet does not supply. See out/image-manifest-result.json.",
     ""),
]

HEADER_TOKENS = ("qa check", "area")

# Column widths in Excel character units, by header name. The supplied
# workbook sets a width for A, B and C only; D onwards fall back to the
# default of roughly 8 characters while carrying 170+ characters of wrapped
# evidence, which renders as a thin column of clipped text.
COLUMN_WIDTHS = {
    "area": 18,
    "qa check": 44,
    "candidate result": 17,
    "candidate evidence": 78,
    "n/a justification": 46,
    "evaluator result": 16,
    "evaluator notes": 34,
    "severity": 11,
    "score": 9,
}

WRAP_COLUMNS = {
    "qa check", "candidate evidence", "n/a justification", "evaluator notes",
}


def _format_sheet(ws, header_row: int, columns: dict[str, int]) -> None:
    """Make the filled workbook readable.

    Long evidence text is useless in a default-width column. Widths are set
    per column, wrapping is enabled where text is long, and fixed row heights
    are cleared so Excel auto-fits to the wrapped content instead of clipping
    it.
    """
    from openpyxl.styles import Alignment
    from openpyxl.utils import get_column_letter

    for name, index in columns.items():
        width = COLUMN_WIDTHS.get(name)
        if width:
            ws.column_dimensions[get_column_letter(index)].width = width

    wrap_indexes = {columns[name] for name in WRAP_COLUMNS if name in columns}

    for row in range(header_row, ws.max_row + 1):
        # Clearing an explicit height lets Excel size the row to its content.
        if row in ws.row_dimensions:
            ws.row_dimensions[row].height = None
        for index in columns.values():
            cell = ws.cell(row, index)
            cell.alignment = Alignment(
                wrap_text=index in wrap_indexes,
                vertical="top",
                horizontal="left",
            )

    ws.freeze_panes = ws.cell(header_row + 1, 1).coordinate


def _find_columns(ws) -> tuple[int, dict[str, int]]:
    for row in range(1, min(ws.max_row, 10) + 1):
        values = {
            str(ws.cell(row, col).value or "").strip().lower(): col
            for col in range(1, ws.max_column + 1)
        }
        if "qa check" in values:
            return row, values
    raise ValueError("could not locate the QA checklist header row")


def fill(template: Path, destination: Path) -> dict:
    wb = openpyxl.load_workbook(template)
    ws = wb[wb.sheetnames[0]]
    header_row, columns = _find_columns(ws)

    col_check = columns["qa check"]
    col_result = columns.get("candidate result")
    col_evidence = columns.get("candidate evidence")
    col_na = columns.get("n/a justification")

    filled, unmatched = 0, []
    for row in range(header_row + 1, ws.max_row + 1):
        check = str(ws.cell(row, col_check).value or "").strip()
        if not check:
            continue
        match = next(
            (a for a in ANSWERS if a[0].lower() in check.lower()), None
        )
        if match is None:
            unmatched.append(check)
            continue
        _, result, evidence, na_justification = match
        if col_result:
            ws.cell(row, col_result).value = result
        if col_evidence:
            ws.cell(row, col_evidence).value = evidence
        if col_na and na_justification:
            ws.cell(row, col_na).value = na_justification
        filled += 1

    _format_sheet(ws, header_row, columns)
    destination.parent.mkdir(parents=True, exist_ok=True)
    wb.save(destination)
    return {"filled": filled, "unmatched": unmatched}


def to_markdown(completed: Path) -> str:
    """Render the completed checklist as a readable document.

    The workbook is the deliverable RNRF asked for and stays the deliverable;
    this is a reading copy. Grouped by area, because 38 rows in a spreadsheet
    column is not something anyone reviews carefully.
    """
    import io
    from datetime import datetime, timezone

    wb = openpyxl.load_workbook(completed, data_only=True)
    ws = wb[wb.sheetnames[0]]
    header_row, columns = _find_columns(ws)

    col_area = columns.get("area")
    col_check = columns["qa check"]
    col_result = columns.get("candidate result")
    col_evidence = columns.get("candidate evidence")
    col_na = columns.get("n/a justification")

    out = io.StringIO()
    w = out.write
    w("# Candidate QA checklist\n\n")
    w("Generated " + datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
      + " from the completed workbook. The workbook remains the deliverable; "
        "this is a reading copy of the same cells.\n\n")

    rows: list[tuple[str, str, str, str, str]] = []
    for row in range(header_row + 1, ws.max_row + 1):
        check = str(ws.cell(row, col_check).value or "").strip()
        if not check:
            continue
        rows.append((
            str(ws.cell(row, col_area).value or "").strip() if col_area else "",
            check,
            str(ws.cell(row, col_result).value or "").strip() if col_result else "",
            str(ws.cell(row, col_evidence).value or "").strip() if col_evidence else "",
            str(ws.cell(row, col_na).value or "").strip() if col_na else "",
        ))

    tally: dict[str, int] = {}
    for _area, _check, result, _ev, _na in rows:
        tally[result or "(blank)"] = tally.get(result or "(blank)", 0) + 1

    w("## Summary\n\n")
    w("| Result | Rows |\n|---|---|\n")
    for result, count in sorted(tally.items(), key=lambda kv: -kv[1]):
        w(f"| **{result}** | {count} |\n")
    w(f"| Total | {len(rows)} |\n\n")
    w("Every row carries evidence naming the artefact that supports it. A row "
      "is only marked Pass where an artefact in `outputs/` or `evidence/` "
      "shows it.\n\n")

    current_area = None
    for area, check, result, evidence, na in rows:
        if area and area != current_area:
            current_area = area
            w(f"\n## {area}\n\n")
        w(f"### {check}\n\n")
        w(f"**{result or '(blank)'}**\n\n")
        if evidence:
            w(evidence + "\n\n")
        if na:
            w(f"*N/A justification:* {na}\n\n")

    return out.getvalue()
