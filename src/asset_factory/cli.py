"""Asset Factory command line.

    validate   parse and validate the Site Variable Sheet only
    audit      read-only audit of the target site
    plan       dry run: validate + audit + propose changes, write nothing
    apply      execute the plan (requires --confirm and credentials)
    rollback   restore from a snapshot

Planning and applying are separate commands on purpose. There is no flag that
turns `plan` into a write.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

from . import evidence as evidence_mod
from . import formstyle
from . import htmlreport
from . import linking
from . import seocheck
from . import imagecmd
from . import prune as prune_mod
from .content import parse_package
from .contentfill import fill_page, split_list_value
from .content import PageContent as _PageContent
from .audit import (
    audit_page,
    brand_tokens,
    check_links,
    find_legacy_brand_assets,
)
from .plan import (
    build_plan,
    derive_focus_keyword,
    keyword_is_traceable,
    load_config,
)
from .report import (
    dry_run_markdown,
    exception_markdown,
    exceptions_csv,
    plan_json,
)
from .sheet import load_variable_sheet, to_json
from .wp import WPClient, WPError

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "out"
SNAPSHOTS = ROOT / "snapshots"


def _client(config: dict) -> WPClient:
    target = config["target"]
    base = target.get("base_url") or ""
    if not base:
        sys.exit("WP_BASE_URL is not set. Copy .env.example to .env first.")
    auth = target.get("auth", {})
    return WPClient(
        base_url=base,
        user=auth.get("user") or None,
        app_password=auth.get("app_password") or None,
    )


def _gather(client: WPClient, config: dict) -> tuple[dict, list]:
    """Collect current site state plus a rendered-page audit for each page."""
    pages = {p["slug"]: p for p in client.pages()}
    site_title = client.site_info().get("name", "")
    state: dict = {"site_title": site_title, "pages": {},
                   "globals": {"site_title": site_title}}
    audits = []

    for spec in config.get("pages", []):
        slug = spec["match"]["slug"]
        page = pages.get(slug)
        if page is None:
            print(f"  ! no page found with slug {slug!r} - skipping",
                  file=sys.stderr)
            continue

        html = client.rendered(page["link"])
        audit = audit_page(
            html, page_id=page["id"], label=f'{spec["page_type"]} page',
            url=page["link"], slug=page["slug"],
            post_title=page["title"]["rendered"],
        )
        audits.append(audit)

        # Record the current rendered state of the global components so the
        # dry run can show what is on the site today, even where no approved
        # replacement exists.
        observed = state["globals"]
        if audit.placeholder_phones:
            observed.setdefault("phone_button_display",
                                sorted(set(audit.placeholder_phones))[0])
        observed.setdefault("phone_button_link",
                            ", ".join(sorted(set(audit.tel_links))) or "(no tel: link)")
        if audit.cta_texts:
            observed.setdefault("primary_cta_text",
                                sorted(set(audit.cta_texts))[0])
        observed.setdefault("form_destination",
                            f"{audit.forms} form(s) present")

        stored = client.seo_meta(page["id"])
        state["pages"][spec["id"]] = {
            "id": page["id"],
            "slug": page["slug"],
            "link": page["link"],
            "post_title": page["title"]["rendered"],
            # Prefer the stored Rank Math value when credentials allow reading
            # it; otherwise fall back to what the page actually renders.
            "rank_math_title": stored.get("rank_math_title")
            or audit.rendered_title,
            "rank_math_description": stored.get("rank_math_description")
            or audit.meta_description,
        }

    # Navigation requires authentication; without it menu changes are planned
    # against the configured labels and reported as unverifiable.
    state["menu_items"] = {}
    if client.authenticated and config.get("menus"):
        try:
            by_slug = {m["slug"]: m for m in client.menus()}
            for menu_cfg in config["menus"]:
                menu = by_slug.get(menu_cfg["menu_slug"])
                if menu is None:
                    print(f"  ! menu {menu_cfg['menu_slug']!r} not found",
                          file=sys.stderr)
                    continue
                for item in client.menu_items(menu["id"]):
                    title = (item.get("title") or {})
                    label = title.get("rendered") if isinstance(title, dict)                         else str(title)
                    if label:
                        state["menu_items"][label.strip()] = item["id"]
        except WPError as exc:
            print(f"  ! could not read menus: {exc}", file=sys.stderr)

    return state, audits


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf8")
    print(f"  wrote {path.relative_to(ROOT)}")


def cmd_validate(args) -> int:
    config = load_config(args.config)
    variables = load_variable_sheet(
        ROOT / config["source"]["variable_sheet"],
        config["source"].get("variable_sheet_tab"),
    )
    summary = variables.summary()
    print(f"Variables: {summary['total']} total, "
          f"{summary['applicable']} applicable")
    for var in variables.exceptions:
        reason = var.primary_reason.value if var.primary_reason else "?"
        print(f"  BLOCKED  {var.key:<34} {reason}")
    for var in variables.applicable:
        for finding in var.advisories:
            print(f"  ADVISORY {var.key:<34} {finding.detail}")
    _write(OUT / "variables.json", to_json(variables))
    return 0


def cmd_audit(args) -> int:
    config = load_config(args.config)
    client = _client(config)
    _, audits = _gather(client, config)
    rows = []
    for audit in audits:
        print(f"\n{audit.label}  ({audit.url})")
        for obs in audit.observations():
            suffix = f"  ({obs.detail})" if obs.detail else ""
            print(f"  {obs.area:<15} {obs.check:<28} {obs.found}{suffix}")
            rows.append(obs.as_row())
    print("\nResolving internal links...")
    links = check_links(audits)
    print(f"  {links['checked']} distinct internal destinations checked, "
          f"{len(links['broken'])} broken")
    for url, entry in links["broken"].items():
        print(f"  BROKEN {url} -> {entry['error'] or entry['status']}")
    _write(OUT / "audit.json", json.dumps(rows, indent=2))
    _write(OUT / "link-check.json", json.dumps(links, indent=2))
    return 0


def cmd_plan(args) -> int:
    config = load_config(args.config)
    variables = load_variable_sheet(
        ROOT / config["source"]["variable_sheet"],
        config["source"].get("variable_sheet_tab"),
    )
    client = _client(config)
    print("Reading site state...")
    state, audits = _gather(client, config)
    plan = build_plan(config, variables, state)

    print(f"\nDRY RUN - nothing written")
    print(f"  {len(plan.changes)} changes proposed")
    print(f"  {len(plan.noops)} already correct")
    print(f"  {len(plan.skips)} skipped and reported\n")
    for change in plan.changes:
        print(f"  ~ {change.target:<20} {change.field:<24} "
              f"{(change.current or '(empty)')[:28]!r} -> {change.proposed[:44]!r}")
    for skip in plan.skips:
        print(f"  - {skip.target:<20} {skip.field:<24} SKIPPED: {skip.reason}")

    _write(OUT / "dry-run.md", dry_run_markdown(plan, variables, audits))
    tokens = brand_tokens(variables.approved("REFERENCE WEBSITE"),
                          variables.approved("{{BRAND_NAME}}"))
    legacy = []
    for page in state.get("pages", {}).values():
        legacy.extend(find_legacy_brand_assets(client.rendered(page["link"]),
                                               tokens))
    if legacy:
        print(f"  ! {len(legacy)} prior-branding asset instance(s) found")
    _write(OUT / "legacy-brand-assets.json", json.dumps(legacy, indent=2))
    _write(OUT / "exception-report.md",
           exception_markdown(plan, variables, audits, legacy))
    _write(OUT / "exception-report.csv", exceptions_csv(plan, variables))
    _write(OUT / "plan.json", plan_json(plan, variables))
    _write(OUT / "variables.json", to_json(variables))
    return 0


def cmd_evidence(args) -> int:
    config = load_config(args.config)
    client = _client(config)
    print(f"Composing before-state from snapshots in {SNAPSHOTS.name}/")
    state, audits = _gather(client, config)
    _write(OUT / "before-after.md",
           evidence_mod.build(SNAPSHOTS, state, audits))
    for path in htmlreport.render_all(OUT):
        print(f"  wrote {path.relative_to(ROOT)}")
    for path in htmlreport.render_pdfs(OUT):
        print(f"  wrote {path.relative_to(ROOT)}")
    return 0


def cmd_content(args) -> int:
    config = load_config(args.config)
    variables = load_variable_sheet(
        ROOT / config["source"]["variable_sheet"],
        config["source"].get("variable_sheet_tab"),
    )
    client = _client(config)
    package = parse_package(ROOT / "inputs" / "content")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pages = {p["slug"]: p for p in client.pages()}
    all_shortfalls, total_applied, total_remaining = [], 0, 0

    for spec in config.get("pages", []):
        key = spec["id"]
        content = package.get(key)
        if content is None:
            print(f"  ! no content document mapped for {key}; skipped")
            continue
        page = pages.get(spec["match"]["slug"])
        if page is None:
            print(f"  ! page {spec['match']['slug']!r} not found; skipped")
            continue

        detail = client._request("GET", f"wp-json/wp/v2/pages/{page['id']}",
                                 params={"context": "edit"})
        raw = detail["content"]["raw"]
        filled, result = fill_page(raw, key, content, variables,
                                    spec.get("h1_variable"))

        print(f"\n{spec['page_type']} page ({page['id']})")
        print(f"  placeholders filled : {result.applied}")
        print(f"  placeholders left   : {result.remaining}")
        for name, count in sorted(result.filled_keys.items()):
            print(f"      {name:<28} x{count}")
        for short in result.shortfalls:
            print(f"      SHORTFALL {short.placeholder:<26} "
                  f"demand {short.demand}, supply {short.supply} "
                  f"({short.source})")
        for note in result.notes:
            print(f"      NOTE {note[:160]}")

        all_shortfalls.extend(result.shortfalls)
        total_applied += result.applied
        total_remaining += result.remaining

        if args.confirm and filled != raw:
            backup = SNAPSHOTS / f"content-{stamp}-{page['id']}.json"
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text(json.dumps({
                "taken": stamp, "page_id": page["id"], "slug": page["slug"],
                "content_raw": raw,
            }, indent=2), encoding="utf8")
            client.update_page_fields(page["id"], content=filled)
            print(f"  SAVED (backup snapshots/{backup.name})")

    # Global template elements (the footer) render on every page, which makes
    # them the most visible place an unresolved value appears.
    for spec in config.get("global_elements", []):
        base = spec["rest_base"]
        items = client._request("GET", f"wp-json/wp/v2/{base}",
                                params={"per_page": 100, "context": "edit"})
        wanted = spec["match_title"].lower()
        target = next(
            (i for i in items
             if wanted in ((i.get("title") or {}).get("raw", "")
                           or (i.get("title") or {}).get("rendered", "")).lower()),
            None,
        )
        if target is None:
            print(f"  ! global element {spec['match_title']!r} not found")
            continue

        queues = {
            placeholder: split_list_value(variables.approved(variable_key) or "")
            for placeholder, variable_key in (spec.get("list_backed") or {}).items()
        }
        synthetic = _PageContent(page=spec["id"], queues=queues)
        raw = target["content"]["raw"]
        filled, result = fill_page(raw, spec["id"], synthetic, variables)

        print(f"\n{spec['match_title']} (global, {base} {target['id']})")
        print(f"  placeholders filled : {result.applied}")
        print(f"  placeholders left   : {result.remaining}")
        for name, count in sorted(result.filled_keys.items()):
            print(f"      {name:<28} x{count}")
        for short in result.shortfalls:
            print(f"      SHORTFALL {short.placeholder:<26} "
                  f"demand {short.demand}, supply {short.supply} "
                  f"({short.source})")
        all_shortfalls.extend(result.shortfalls)
        total_applied += result.applied
        total_remaining += result.remaining

        if args.confirm and filled != raw:
            backup = SNAPSHOTS / f"global-{stamp}-{target['id']}.json"
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text(json.dumps({
                "taken": stamp, "post_id": target["id"],
                "rest_base": base, "content_raw": raw,
            }, indent=2), encoding="utf8")
            client._request("POST", f"wp-json/wp/v2/{base}/{target['id']}",
                            json={"content": filled})
            print(f"  SAVED (backup snapshots/{backup.name})")

    print(f"\nTOTAL filled {total_applied}, remaining {total_remaining}")
    _write(OUT / "content-shortfalls.json", json.dumps([
        {"page": s.page, "placeholder": s.placeholder, "demand": s.demand,
         "supply": s.supply, "missing": s.missing, "source": s.source}
        for s in all_shortfalls], indent=2))
    if not args.confirm:
        print("DRY RUN - nothing written. Re-run with --confirm.")
    return 0


def cmd_formstyle(args) -> int:
    """Make the Fluent Forms confirmation message legible on every page.

    Appends one rule to the Fluent Forms block's own customCss attribute,
    which lives in page content. The colour is read back from the rule the
    template author already wrote for this form's labels, so nothing is
    invented and no Global Style is touched.
    """
    config = load_config(args.config)
    client = _client(config)
    pages = {p["slug"]: p for p in client.pages()}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log, changed = [], 0

    for spec in config.get("pages", []):
        page = pages.get(spec["match"]["slug"])
        if page is None:
            continue
        detail = client._request("GET", f"wp-json/wp/v2/pages/{page['id']}",
                                 params={"context": "edit"})
        raw = detail["content"]["raw"]
        updated, result = formstyle.apply_to_content(raw)

        mark = "+" if result.changed else "="
        colour = f" ({result.colour})" if result.colour else ""
        print(f"  {mark} {spec['page_type']:<10} {result.reason}{colour}")
        log.append({"page": spec["id"], "changed": result.changed,
                    "reason": result.reason, "colour": result.colour})

        if result.changed and args.confirm:
            backup = SNAPSHOTS / f"formstyle-{stamp}-{page['id']}.json"
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text(json.dumps({
                "taken": stamp, "page_id": page["id"], "slug": page["slug"],
                "content_raw": raw}, indent=2), encoding="utf8")
            client.update_page_fields(page["id"], content=updated)
            after = client._request(
                "GET", f"wp-json/wp/v2/pages/{page['id']}",
                params={"context": "edit"})["content"]["raw"]
            if formstyle.verify(after):
                changed += 1
                print(f"      SAVED and verified "
                      f"(backup snapshots/{backup.name})")
            else:
                print("      ! saved but verification failed")

    _write(OUT / "form-style.json", json.dumps(log, indent=2))
    if not args.confirm:
        print("\nDRY RUN - nothing written. Re-run with --confirm.")
    else:
        print(f"\n{changed} page(s) updated and verified.")
    return 0


def cmd_seo(args) -> int:
    """Audit every in-scope page against Rank Math's on-page checks."""
    config = load_config(args.config)
    variables = load_variable_sheet(
        ROOT / config["source"]["variable_sheet"],
        config["source"].get("variable_sheet_tab"),
    )
    client = _client(config)
    pages = {p["slug"]: p for p in client.pages()}
    used: set[str] = set()
    report: list[dict] = []

    for spec in config.get("pages", []):
        source_key = spec.get("focus_keyword_from")
        page = pages.get(spec["match"]["slug"])
        if not (source_key and page):
            continue
        approved = variables.approved(source_key)
        html = client.rendered(page["link"])
        keyword, result, ranked = seocheck.select_keyword(
            html, page["link"], approved, used, keyword_is_traceable)
        used.add(seocheck._norm(keyword))

        fixable = [c for c in result.failures if c.fixable]
        print(f"\n{spec['page_type']}  {result.score}% "
              f"({result.passed}/{result.total})   keyword {keyword!r}")
        print(f"  derived from {source_key} = {approved!r}")
        for check in result.failures:
            tag = "FIXABLE" if check.fixable else "blocked"
            print(f"    [{tag:<7}] {check.name}")
            if check.blocked_by:
                print(f"              {check.blocked_by}")
        print(f"  {len(fixable)} failure(s) fixable without editing "
              "approved content")

        report.append({
            "page": spec["id"], "url": page["link"], "keyword": keyword,
            "keyword_source": source_key, "approved_source_value": approved,
            "score_percent": result.score,
            "passed": result.passed, "total": result.total,
            "candidates_considered": [
                {"keyword": k, "score": s} for s, k, _ in ranked],
            "checks": [
                {"name": c.name, "group": c.group, "passed": c.passed,
                 "detail": c.detail, "blocked_by": c.blocked_by,
                 "fixable": c.fixable}
                for c in result.checks],
        })

    average = (round(sum(r["score_percent"] for r in report) / len(report))
               if report else 0)
    fixable_left = sum(1 for r in report for c in r["checks"] if c["fixable"])
    print(f"\nAverage {average}% across {len(report)} pages. "
          f"{fixable_left} fixable failure(s) remain site-wide.")
    _write(OUT / "seo-audit.json", json.dumps(
        {"average_score_percent": average, "pages": report}, indent=2))
    return 0


def cmd_links(args) -> int:
    """Carry out the sheet's approved internal-linking instructions."""
    config = load_config(args.config)
    variables = load_variable_sheet(
        ROOT / config["source"]["variable_sheet"],
        config["source"].get("variable_sheet_tab"),
    )
    client = _client(config)
    brand = variables.approved("{{BRAND_NAME}}")
    if not brand:
        sys.exit("{{BRAND_NAME}} is not Approved; no link can be made.")

    pages = {p["slug"]: p for p in client.pages()}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log, changed_total = [], 0

    for spec in config.get("pages", []):
        instruction_key = spec.get("first_brand_link")
        if not instruction_key:
            continue
        instruction = variables.get(instruction_key)
        if instruction is None or not instruction.applicable:
            print(f"  = {spec['page_type']}: {instruction_key} is not "
                  "Approved; skipped")
            continue

        page = pages.get(spec["match"]["slug"])
        if page is None:
            print(f"  ! page {spec['match']['slug']!r} not found")
            continue

        detail = client._request("GET", f"wp-json/wp/v2/pages/{page['id']}",
                                 params={"context": "edit"})
        raw = detail["content"]["raw"]
        updated, result = linking.link_first_brand_mention(raw, brand)

        mark = "+" if result.changed else "="
        print(f"  {mark} {spec['page_type']:<10} {result.reason}")
        if result.context:
            print(f"      ...{result.context.strip()[:96]}...")
        log.append({"page": spec["id"], "instruction": instruction_key,
                    "changed": result.changed, "reason": result.reason})

        if result.changed and args.confirm:
            backup = SNAPSHOTS / f"links-{stamp}-{page['id']}.json"
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text(json.dumps({
                "taken": stamp, "page_id": page["id"], "slug": page["slug"],
                "content_raw": raw}, indent=2), encoding="utf8")
            client.update_page_fields(page["id"], content=updated)
            # An instruction reported as carried out must be verifiable.
            after = client._request(
                "GET", f"wp-json/wp/v2/pages/{page['id']}",
                params={"context": "edit"})["content"]["raw"]
            if linking.verify(after, brand):
                changed_total += 1
                print(f"      SAVED and verified "
                      f"(backup snapshots/{backup.name})")
            else:
                print("      ! saved but verification failed")

    _write(OUT / "internal-links.json", json.dumps(log, indent=2))
    if not args.confirm:
        print("\nDRY RUN - nothing written. Re-run with --confirm.")
    else:
        print(f"\n{changed_total} page(s) updated and verified.")
    return 0


def cmd_prune(args) -> int:
    """Remove template card instances that hold no approved content."""
    config = load_config(args.config)
    client = _client(config)
    sections = (config.get("prune") or {}).get("sections") or {}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    pages = {p["slug"]: p for p in client.pages()}
    report, total = [], 0

    for spec in config.get("pages", []):
        key = spec["id"]
        page = pages.get(spec["match"]["slug"])
        if page is None:
            continue
        detail = client._request("GET", f"wp-json/wp/v2/pages/{page['id']}",
                                 params={"context": "edit"})
        raw = detail["content"]["raw"]
        before = len(prune_mod.PLACEHOLDER.findall(raw))

        stage1 = prune_mod.find_removable(raw)
        interim, ok1 = prune_mod.apply_removals(raw, stage1)
        named = [e["slot"] for e in sections.get(key, [])]
        expect = {e["slot"]: e["expect"] for e in sections.get(key, [])}
        stage2 = prune_mod.find_orphan_sections(interim, named, expect)
        interim2, ok2 = prune_mod.apply_removals(interim, stage2)

        dup_cfg = (config.get("prune") or {}).get("duplicates") or {}
        dup_named = [e["slot"] for e in dup_cfg.get(key, [])]
        dup_expect = {e["slot"]: e["expect"] for e in dup_cfg.get(key, [])}
        stage3 = prune_mod.find_duplicate_sections(interim2, dup_named,
                                                   dup_expect)
        final, ok3 = prune_mod.apply_removals(interim2, stage3)
        ok2 = ok2 and ok3
        after = len(prune_mod.PLACEHOLDER.findall(final))

        print(f"\n{spec['page_type']} page ({page['id']})")
        print(f"  placeholders {before} -> {after}   "
              f"bytes {len(raw)} -> {len(final)}   balanced={ok1 and ok2}")
        for removal in stage1 + stage2 + stage3:
            print(f"    - {removal.block:<15} {removal.unique_id} "
                  f"({removal.length}b)  {removal.text[:52]!r}")
            report.append({"page": key, "kind": removal.block,
                           "uniqueId": removal.unique_id,
                           "bytes": removal.length, "text": removal.text,
                           "placeholders": removal.placeholders})
        total += len(stage1) + len(stage2) + len(stage3)

        if not (ok1 and ok2):
            print("    ! block comments would not balance; refusing to save")
            continue
        if args.confirm and final != raw:
            backup = SNAPSHOTS / f"prune-{stamp}-{page['id']}.json"
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text(json.dumps({
                "taken": stamp, "page_id": page["id"], "slug": page["slug"],
                "content_raw": raw,
            }, indent=2), encoding="utf8")
            client.update_page_fields(page["id"], content=final)
            print(f"    SAVED (backup snapshots/{backup.name})")

    _write(OUT / "prune-report.json", json.dumps(report, indent=2))
    print(f"\n{total} block(s) removable.")
    if not args.confirm:
        print("DRY RUN - nothing written. Re-run with --confirm.")
    return 0


def cmd_images(args) -> int:
    config = load_config(args.config)
    client = _client(config)
    manifest = Path(args.manifest or (ROOT / "config" / "images.yml"))
    imagecmd.run(client, config, manifest, phase=args.phase,
                 confirm=args.confirm, out_dir=OUT, root=ROOT)
    return 0


def cmd_apply(args) -> int:
    if not args.confirm:
        sys.exit("apply requires --confirm. Run `plan` first and read the dry run.")

    config = load_config(args.config)
    variables = load_variable_sheet(
        ROOT / config["source"]["variable_sheet"],
        config["source"].get("variable_sheet_tab"),
    )
    client = _client(config)

    health = client.verify_write_path()
    print("Write-path pre-flight:", health)
    if not health.get("authenticated"):
        sys.exit("No credentials. Set WP_USER and WP_APP_PASSWORD in .env.")
    if not health.get("rankmath/v1"):
        sys.exit("Rank Math REST namespace missing; SEO fields cannot be written.")

    state, audits = _gather(client, config)
    plan = build_plan(config, variables, state)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    snapshot = {
        "taken": stamp,
        "site_title": state["site_title"],
        "pages": state["pages"],
        "menu_items": state.get("menu_items", {}),
    }
    _write(SNAPSHOTS / f"{stamp}.json", json.dumps(snapshot, indent=2))

    applied, skipped, reasserted = 0, 0, 0
    for change in plan.changes:
        if change.is_noop:
            skipped += 1
            continue
        try:
            if change.target_kind == "option":
                client.update_settings(title=change.proposed)
            elif change.target_kind == "page":
                if change.field == "post_title":
                    client.update_page_fields(change.target_id,
                                              title=change.proposed)
                elif change.field.startswith("rank_math_"):
                    client.update_rank_math(change.target_id,
                                            **{change.field: change.proposed})
                else:
                    print(f"  ? unhandled field {change.field}; skipped")
                    skipped += 1
                    continue
            elif change.target_kind == "menu_item":
                if change.target_id is None:
                    print(f"  ! {change.target}: no live menu item matching "
                          f"{change.current!r}; skipped")
                    skipped += 1
                    continue
                client.update_menu_item(change.target_id, title=change.proposed)
            else:
                print(f"  ? unhandled target kind {change.target_kind}; skipped")
                skipped += 1
                continue
            applied += 1
            print(f"  + {change.target} / {change.field}")
        except WPError as exc:
            skipped += 1
            print(f"  ! FAILED {change.target} / {change.field}: {exc}")

    # Focus keywords. The sheet supplies no keyword field, so each page's
    # keyword is taken from an Approved value -- verbatim, or with trailing
    # generic nouns trimmed. A guard refuses to write any keyword containing a
    # word that is not already in its source cell, which is what makes this a
    # derivation rather than an invention. Rank Math's focus keyword is an
    # editorial field and renders nowhere on the site.
    _keywords_used: set[str] = set()
    for spec in config.get("pages", []):
        source_key = spec.get("focus_keyword_from")
        if not source_key:
            continue
        page_state = state.get("pages", {}).get(spec["id"], {})
        page_id = page_state.get("id")
        approved = variables.approved(source_key)
        if not (page_id and approved):
            print(f"  = {spec['page_type']}: focus keyword source "
                  f"{source_key} is not Approved; skipped")
            skipped += 1
            continue
        # Choose among the derivations this Approved value permits, scoring
        # each against the same checks Rank Math runs. Falls back to the plain
        # trim if the page cannot be fetched.
        try:
            html = client.rendered(page_state["link"])
            keyword, _result, _ranked = seocheck.select_keyword(
                html, page_state["link"], approved, _keywords_used,
                keyword_is_traceable)
        except Exception:
            keyword = derive_focus_keyword(approved)
        _keywords_used.add(seocheck._norm(keyword))
        if not keyword_is_traceable(keyword, approved):
            print(f"  ! {spec['page_type']}: derived keyword {keyword!r} is "
                  f"not traceable to {source_key}; refusing to write")
            skipped += 1
            continue
        try:
            client.update_rank_math(page_id,
                                    rank_math_focus_keyword=keyword)
            # Counted separately from `applied`. Rank Math's focus keyword is
            # write-only over REST -- it is absent from the meta payload even
            # in edit context -- so the workflow cannot tell a first write
            # from a repeat. Reporting it as a change on every run would make
            # a repeat run look like it modified something. It is reported as
            # re-asserted instead, which is exactly what it is.
            reasserted += 1
            print(f"  = {spec['page_type']} / focus keyword re-asserted "
                  f"{keyword!r} (from {source_key})")
        except WPError as exc:
            skipped += 1
            print(f"  ! FAILED {spec['page_type']} focus keyword: {exc}")

    print(f"\nApplied {applied}, skipped {skipped}, "
          f"re-asserted {reasserted}. Snapshot: snapshots/{stamp}.json")
    if reasserted:
        print("  (re-asserted = written to a field Rank Math does not expose "
              "for reading over REST, so a repeat write cannot be "
              "distinguished from the first; not counted as a change)")

    print("Re-reading site to verify...")
    state_after, audits_after = _gather(client, config)
    plan_after = build_plan(config, variables, state_after)
    remaining = [c for c in plan_after.changes if not c.is_noop]
    if remaining:
        print(f"  ! {len(remaining)} change(s) did not take effect:")
        for change in remaining:
            print(f"    {change.target} / {change.field}")
    else:
        print("  all proposed changes verified present.")

    _write(OUT / "post-apply-audit.json",
           json.dumps([o.as_row() for a in audits_after
                       for o in a.observations()], indent=2))
    return 0 if not remaining else 1


def cmd_rollback(args) -> int:
    config = load_config(args.config)
    client = _client(config)
    path = Path(args.snapshot)
    if not path.exists():
        path = SNAPSHOTS / args.snapshot
    snapshot = json.loads(path.read_text(encoding="utf8"))

    if not args.confirm:
        print(f"Would restore from {path.name}:")
        print(f"  site title -> {snapshot['site_title']!r}")
        for key, page in snapshot["pages"].items():
            print(f"  {key} title -> {page['post_title']!r}")
        print("\nRe-run with --confirm to restore.")
        return 0

    client.update_settings(title=snapshot["site_title"])
    for label, item_id in snapshot.get("menu_items", {}).items():
        client.update_menu_item(item_id, title=label)
    for page in snapshot["pages"].values():
        client.update_page_fields(page["id"], title=page["post_title"])
        client.update_rank_math(
            page["id"],
            rank_math_title=page.get("rank_math_title", ""),
            rank_math_description=page.get("rank_math_description", ""),
        )
    print(f"Restored from {path.name}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser(prog="asset-factory")
    parser.add_argument("--config", default=str(ROOT / "config" / "site.yml"))
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate").set_defaults(func=cmd_validate)
    sub.add_parser("audit").set_defaults(func=cmd_audit)
    sub.add_parser("plan").set_defaults(func=cmd_plan)

    evidence_parser = sub.add_parser("evidence")
    evidence_parser.add_argument("--snapshot", default=None)
    evidence_parser.set_defaults(func=cmd_evidence)

    content_parser = sub.add_parser("content")
    content_parser.add_argument("--confirm", action="store_true")
    content_parser.set_defaults(func=cmd_content)

    formstyle_parser = sub.add_parser("formstyle")
    formstyle_parser.add_argument("--confirm", action="store_true")
    formstyle_parser.set_defaults(func=cmd_formstyle)

    sub.add_parser("seo").set_defaults(func=cmd_seo)

    links_parser = sub.add_parser("links")
    links_parser.add_argument("--confirm", action="store_true")
    links_parser.set_defaults(func=cmd_links)

    prune_parser = sub.add_parser("prune")
    prune_parser.add_argument("--confirm", action="store_true")
    prune_parser.set_defaults(func=cmd_prune)

    images_parser = sub.add_parser("images")
    images_parser.add_argument("--phase", type=int, default=1, choices=[1, 2])
    images_parser.add_argument("--manifest", default=None)
    images_parser.add_argument("--confirm", action="store_true")
    images_parser.set_defaults(func=cmd_images)

    apply_parser = sub.add_parser("apply")
    apply_parser.add_argument("--confirm", action="store_true")
    apply_parser.set_defaults(func=cmd_apply)

    rollback_parser = sub.add_parser("rollback")
    rollback_parser.add_argument("snapshot")
    rollback_parser.add_argument("--confirm", action="store_true")
    rollback_parser.set_defaults(func=cmd_rollback)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
