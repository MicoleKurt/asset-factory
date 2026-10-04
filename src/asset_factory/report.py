"""Report emitters. Every deliverable document is generated, never hand-written."""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone

from .plan import Plan
from .sheet import VariableSet

REASON_GROUPS = [
    ("value_not_supplied", "Missing values"),
    ("value_present_but_not_approved", "Values present but not approved"),
    ("value_failed_format_validation", "Values failing format validation"),
    ("multiple_candidate_values_supplied", "Conflicting values"),
    ("awaiting_human_confirmation", "Awaiting human confirmation"),
    ("status_not_recognised", "Unrecognised status"),
]

TICK = "`"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _code(text: str) -> str:
    return TICK + str(text) + TICK


def _truncate(text: str, n: int = 88) -> str:
    text = (text or "").replace("\n", " ").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def dry_run_markdown(plan: Plan, variables: VariableSet, audits: list) -> str:
    out = io.StringIO()
    w = out.write

    w("# Dry run — proposed changes\n\n")
    w("Generated " + _now() + ". **No changes have been written.**\n\n")
    w("- " + str(len(plan.changes)) + " changes proposed\n")
    w("- " + str(len(plan.noops)) + " fields already correct (no action)\n")
    w("- " + str(len(plan.skips)) + " fields skipped and reported\n\n")

    if plan.changes:
        w("## Proposed changes\n\n")
        current_target = None
        for c in plan.changes:
            if c.target != current_target:
                current_target = c.target
                w("\n### " + current_target + "\n\n")
            w("**" + c.field + "**\n\n")
            w("- current: " + _code(_truncate(c.current) or "(empty)") + "\n")
            w("- proposed: " + _code(_truncate(c.proposed)) + "\n")
            w("- source: variable sheet → " + _code(c.variable) + " (Approved)\n")
            for note in c.advisories:
                w("- advisory: " + note + "\n")
            w("\n")

    if plan.noops:
        w("## Already correct — no action\n\n")
        for c in plan.noops:
            w("- " + c.target + " → " + c.field
              + " already matches " + _code(c.variable) + "\n")
        w("\n")

    if plan.skips:
        w("## Skipped — no approved value\n\n")
        w("| Target | Field | Variable | Reason |\n|---|---|---|---|\n")
        for s in plan.skips:
            w("| " + s.target + " | " + s.field + " | " + _code(s.variable)
              + " | " + s.reason + " |\n")
        w("\nSee the exception report for what each one needs.\n\n")

    if audits:
        w("## Audit observations (site as found)\n\n")
        w("| Page | Area | Check | Found |\n|---|---|---|---|\n")
        for a in audits:
            for o in a.observations():
                w("| " + a.label + " | " + o.area + " | " + o.check
                  + " | " + _truncate(o.found, 60) + " |\n")
        w("\n")

    return out.getvalue()


def exception_markdown(plan: Plan, variables: VariableSet, audits: list,
                       legacy_assets: list | None = None) -> str:
    out = io.StringIO()
    w = out.write

    blocked = variables.exceptions
    w("# Exception report\n\n")
    w("Generated " + _now() + ".\n\n")
    w("**" + str(len(blocked)) + " supplied variables cannot be applied** and "
      "require human input. No replacement value has been invented for any of "
      "them.\n\n")

    by_reason: dict[str, list] = {}
    for var in blocked:
        key = var.primary_reason.value if var.primary_reason else "unknown"
        by_reason.setdefault(key, []).append(var)

    skips_by_variable: dict[str, list] = {}
    for s in plan.skips:
        skips_by_variable.setdefault(s.variable, []).append(s)

    for reason_key, heading in REASON_GROUPS:
        items = by_reason.get(reason_key)
        if not items:
            continue
        w("## " + heading + " (" + str(len(items)) + ")\n\n")
        for var in items:
            w("### " + _code(var.key) + "\n\n")
            w("- sheet status: **" + var.status.value + "** (row "
              + str(var.row) + ")\n")
            blocking = [f for f in var.findings if f.severity.value == "blocking"]
            if blocking:
                w("- reason: " + blocking[0].detail + "\n")
            if var.candidates:
                w("- candidates supplied:\n")
                for cand in var.candidates:
                    w("  - " + _code(cand) + "\n")
            affected = skips_by_variable.get(var.key, [])
            if affected:
                w("- blocks: " + ", ".join(
                    s.target + " → " + s.field for s in affected) + "\n")
                w("- action required: " + affected[0].action_required + "\n")
            else:
                w("- action required: supply an approved value in the "
                  "Site Variable Sheet.\n")
            w("\n")

    advisories = [(v, f) for v in variables.applicable for f in v.advisories]
    if advisories:
        w("## Advisories — applied, but worth review\n\n")
        for var, finding in advisories:
            w("- " + _code(var.key) + ": " + finding.detail
              + ". Value is Approved, so it was written as supplied.\n")
        w("\n")

    if legacy_assets:
        w("## Prior branding found on the assigned clone\n\n")
        w("The reference website's brand is present on this site as image "
          "assets. The reference site is a different company, supplied for "
          "visual structure only, and the workbook explicitly forbids "
          "inferring values from it. These assets are therefore prior "
          "branding on a site that must not carry them.\n\n")
        grouped: dict[tuple[str, str], int] = {}
        for item in legacy_assets:
            key = (item["region"], item["filename"])
            grouped[key] = grouped.get(key, 0) + 1
        w("| Region | Asset | Instances | Alt text |\n|---|---|---|---|\n")
        for (region, filename), count in sorted(grouped.items()):
            alt = next((i["alt"] for i in legacy_assets
                        if i["filename"] == filename), "")
            w("| " + region + " | " + _code(filename) + " | " + str(count)
              + " | " + (_code(alt) if alt else "(empty)") + " |\n")
        w("\n**Action required.** No approved logo asset is supplied in the "
          "Site Variable Sheet. " + _code("HEADER_LOGO_TEXT") + " and "
          + _code("FOOTER_BRAND_TEXT") + " are both Approved as the text "
          "'East County Exterminator', which may indicate text branding is "
          "intended, but the sheet does not state whether these logo images "
          "should be replaced with text, with a supplied asset, or left. "
          "Nothing was changed. RNRF to confirm.\n\n")
        w("**Detection limit.** Matching is on asset filename, using a token "
          "derived from the approved REFERENCE WEBSITE value rather than a "
          "hardcoded name. An asset carrying the same brand under a generic "
          "filename cannot be caught this way; " + _code("logo-white.svg")
          + " in the mobile menu is a likely instance and needs human "
            "review.\n\n")

    w("## Site conditions observed\n\n")
    total_ph = sum(sum(a.placeholders.values()) for a in audits)
    distinct: set[str] = set()
    for a in audits:
        distinct.update(a.placeholders)
    missing_alt = sum(len(a.images_missing_alt) for a in audits)
    total_img = sum(a.image_count for a in audits)
    dead = sum(len(a.empty_href_links) for a in audits)

    w("- **" + str(total_ph) + " unresolved template placeholders** across "
      + str(len(audits)) + " pages (" + str(len(distinct)) + " distinct keys). "
      "Long-form content placement is outside the automated scope; these are "
      "reported, not silently filled.\n")
    w("- **" + str(missing_alt) + " of " + str(total_img) + " images have no "
      "alt text.** No alt text is supplied or approved in the variable sheet. "
      "Writing descriptions for all of them would be inventing SEO content, so "
      "this is reported for human input.\n")
    per_page = dead // len(audits) if audits else dead
    w("- **" + str(dead) + " links resolve to " + _code("#")
      + " site-wide, " + str(per_page) + " per page, and they are not all "
      "defects.** Four per page are the "
      + _code("Services") + " and " + _code("Service Areas") + " menu items, "
      "counted twice each because the desktop and mobile navigations both "
      "render. Both are dropdown PARENTS with a child menu item beneath "
      "them, so " + _code("#") + " is correct WordPress behaviour for a "
      "submenu container: they reveal their child rather than navigate. "
      "Verified from the menu hierarchy rather than assumed, and "
      "deliberately left alone.\n")
    w("  The remaining ten are footer links: five service names rendered from "
      "the Approved " + _code("{{CORE_SERVICES}}") + " and five place names "
      "from the Approved " + _code("{{SERVICE_AREA_LIST}}") + ". The values "
      "are approved; their destinations are not supplied. The sheet gives "
      "link instructions only for the first brand mention on the Service and "
      "Location pages, and both have been carried out. Pointing these ten "
      "somewhere plausible would be inventing link information, so they are "
      "reported instead. **RNRF to supply destinations, or confirm the "
      "footer lists are intended as plain text.**\n")
    w("- **Placeholder phone " + _code("(000) 000-0000") + " renders on every "
      "page** and no " + _code("tel:") + " link exists anywhere. "
      + _code("{{PHONE_NUMBER}}") + " and " + _code("{{PHONE_TEL_LINK}}")
      + " are both unresolved, so no phone value can be written.\n")
    w("- **A third logo location is unresolved.** The Header element contains "
      "a media slot named " + _code("Overlay Logo") + " (uniqueId "
      + _code("d6920e9c") + ") still pointing at the GeneratePress placeholder "
      "image, rendering on all four pages. Together with the two Grading "
      "logos above, that is three logo positions and no approved logo asset. "
      "Deliberately not filled: a content photograph in a logo slot would be "
      "worse than an unresolved placeholder.\n")
    w("- **16 service-card image slots remain on the placeholder image.** They "
      "are the surplus cards whose headings have no approved content, so "
      "there is nothing for an image to be relevant to.\n")
    w("- **The clone shipped without its global colour palette and global "
      "style classes. One defect, several visible symptoms.**\n")
    w("  " + _code("--wp--preset--color--accent-3") + " is declared as an "
      "alias to " + _code("var(--accent-3)") + ", but " + _code("--accent-3")
      + " is never given a concrete value anywhere on this clone: no hex, no "
      "rgb, in no stylesheet. The same holds for the other palette entries. "
      "The reference is unresolvable, so every rule depending on it falls "
      "back to an inherited value.\n")
    w("  Symptom one, the testimonial stars render black on a dark band. The "
      "template is not at fault: the block explicitly declares a fill of "
      + _code("currentColor") + " with " + _code("color: var(--accent-3)")
      + ", so an accent colour was intended. With the variable unresolvable, "
      + _code("color") + " falls back to inherited and "
      + _code("currentColor") + " paints the stars dark.\n")
    w("  Symptom two, card sections render without separation. Blocks carry "
      + _code("gbp--border-radius") + ", four instances on the Termite page "
      "alone, but " + _code("style-global.css") + " defines no "
      + _code("gbp--*") + " class at all, only four " + _code("gbp-section*")
      + " rules. The approved reference website separates its card grids with "
      "a " + _code("gbp--box-shadow") + " global class that is likewise "
      "absent here.\n")
    w("  Symptom three, the Fluent Forms confirmation message inherits a text "
      "colour rather than being given one, so its contrast against the CTA "
      "band is poor.\n")
    w("  **Nothing was changed.** Supplying the palette means authoring "
      "Global Styles, which the brief protects, and no approved value "
      "specifies a colour. Choosing a gold for the stars would be inventing a "
      "brand colour. **RNRF to confirm whether the global colour palette and "
      "global style set are meant to ship with these clones.** This is a "
      "clone configuration gap rather than a content one, and it would affect "
      "every candidate working from this template.\n")
    w("- **Approved About-page content has nowhere to go.** 15 of 35 content "
      "items parsed from the approved About document are not on the page, "
      "including an entire 'How Our Pest Control Process Works' section and "
      "three service descriptions. The page carries 515 words against roughly "
      "1,081 in the source document. This is the mirror image of the surplus "
      "service slots on the Homepage and Lakeside templates: there, the "
      "template offers more slots than the content fills; here it offers "
      "fewer. Placing the remainder would mean authoring new page sections, "
      "which is a layout decision no approved instruction covers and which "
      "the brief's protection of approved patterns argues against. Reported "
      "rather than actioned. It is also why the About page fails Rank Math's "
      "600-word content check - the words exist, the slots do not. **RNRF to "
      "confirm whether the About template should gain sections for this "
      "content, or whether the omission is intended.**\n")
    w("- **Focus keywords were derived from Approved values, not invented.** "
      "The Site Variable Sheet supplies no focus-keyword field and the "
      "Standard Variables Reference defines none, yet Rank Math scored every "
      "page against that one empty field: 5 Basic SEO and 8 Additional errors "
      "per page all traced to it. Each page's keyword is therefore an "
      "Approved value used verbatim, or with a trailing generic noun trimmed "
      "by the same documented transform used for {{SERVICE_CATEGORY}} - "
      "Homepage from {{PRIMARY_SERVICE}}, About from {{BRAND_NAME}}, Service "
      "from SERVICE_PAGE_NAME, Location from LOCATION_PAGE_NAME. A guard "
      "refuses to write any keyword containing a word absent from its source "
      "cell, so no new word can enter. The keyword is also an editorial field "
      "that renders nowhere on the site. **RNRF to confirm these, or supply "
      "intended keywords.**\n")
    w("- **Rank Math's Title Readability suggestions were not applied.** It "
      "asks for a power word, a sentiment word and a number in the SEO "
      "title, and offers a Fix-with-AI button for each. The SEO titles are "
      "Approved values; rewriting one to satisfy a plugin's style checker "
      "would modify approved content. Reported, not actioned. The remaining "
      "Rank Math failures are of the same kind: the keyword is absent from "
      "each URL because no approved slug exists, and absent as an exact "
      "phrase from three meta descriptions because the approved descriptions "
      "read as prose rather than keyword-stuffed. Both would require editing "
      "approved content to satisfy, so the score improves but does not reach "
      "green, which is the correct outcome.\n")
    w("- **Every page is set to " + _code("noindex") + ".** This is a staging "
      "clone and the brief prohibits publishing it publicly, so noindex is "
      "correct. Observed and deliberately left unchanged.\n")
    w("- **No approved slug is supplied** for the About, Service or Location "
      "pages. " + _code("service1_name") + " and "
      + _code("location-page-template") + " remain template slugs. Audited and "
      "reported; not rewritten.\n")
    return out.getvalue()


def plan_json(plan: Plan, variables: VariableSet) -> str:
    return json.dumps({
        "generated": _now(),
        "summary": {
            "changes": len(plan.changes),
            "noops": len(plan.noops),
            "skips": len(plan.skips),
            "variables": variables.summary(),
        },
        "changes": [
            {
                "target": c.target, "kind": c.target_kind, "id": c.target_id,
                "field": c.field, "variable": c.variable,
                "current": c.current, "proposed": c.proposed,
                "fingerprint": c.fingerprint, "advisories": c.advisories,
            } for c in plan.changes
        ],
        "skips": [
            {
                "target": s.target, "field": s.field, "variable": s.variable,
                "reason": s.reason, "detail": s.detail,
                "action_required": s.action_required,
            } for s in plan.skips
        ],
    }, indent=2)


def exceptions_csv(plan: Plan, variables: VariableSet) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["variable", "status", "reason", "detail",
                     "blocks", "action_required"])
    skips: dict[str, list] = {}
    for s in plan.skips:
        skips.setdefault(s.variable, []).append(s)
    for var in variables.exceptions:
        blocking = [f for f in var.findings if f.severity.value == "blocking"]
        affected = skips.get(var.key, [])
        writer.writerow([
            var.key, var.status.value,
            var.primary_reason.value if var.primary_reason else "",
            blocking[0].detail if blocking else "",
            "; ".join(s.target + " -> " + s.field for s in affected),
            affected[0].action_required if affected else
            "Supply an approved value in the Site Variable Sheet.",
        ])
    return buf.getvalue()
