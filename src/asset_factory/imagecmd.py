"""`images` subcommand: optimise, upload and place the image manifest."""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import images as img
from .plan import load_config

HERO_CLASS = "ap-hero-image"
MEDIA_BLOCK = re.compile(r'<!-- wp:generateblocks/media (\{.*?\}) -->', re.S)


def hero_slot(raw: str) -> tuple[str | None, str]:
    """Identify the hero image slot and say how it was identified.

    Preference order, most reliable first:
      1. the block carrying the theme's ap-hero-image class
      2. the first placeholder media block in document order
    The method is returned so the change log can record it rather than implying
    a certainty the markup does not support.
    """
    candidates = []
    for match in MEDIA_BLOCK.finditer(raw):
        try:
            attrs = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        # Deliberately NOT filtered to placeholder slots. A hero that has
        # already been repointed must still be findable, or a follow-up run
        # cannot correct anything about it -- such as missing alt text.
        classes = attrs.get("globalClasses") or []
        candidates.append((attrs.get("uniqueId"), classes))

    for unique_id, classes in candidates:
        if HERO_CLASS in classes:
            return unique_id, f"matched .{HERO_CLASS}"
    if candidates:
        return candidates[0][0], "first placeholder slot in document order"
    return None, "no placeholder slot found"


def run(client, config: dict, manifest_path: Path, *, phase: int,
        confirm: bool, out_dir: Path, root: Path) -> dict:
    manifest = load_config(manifest_path)
    settings = manifest.get("settings", {})
    source_dir = (root / settings.get("source_dir", "../")).resolve()

    roles = set(manifest["placement"][f"phase_{phase}_roles"])
    page_ids = {spec["id"]: spec for spec in config.get("pages", [])}

    assets: list[img.Asset] = []
    problems: list[str] = []

    for entry in manifest["assets"]:
        asset = img.Asset(
            source=source_dir / entry["source"],
            filename=entry["filename"],
            alt=" ".join(entry["alt"].split()),
            role=entry["role"],
            page=entry["page"],
        )
        for check, value in (
            (img.validate_filename, asset.filename),
            (img.validate_alt, asset.alt),
        ):
            issue = check(value)
            if issue:
                problems.append(f"{entry['source']}: {issue}")
        if not asset.source.exists():
            problems.append(f"{entry['source']}: file not found at {asset.source}")
        if asset.page not in page_ids:
            problems.append(f"{entry['source']}: unknown page id {asset.page!r}")
        assets.append(asset)

    if problems:
        for problem in problems:
            print(f"  ! {problem}")
        raise SystemExit("manifest validation failed; nothing was uploaded")

    print(f"Manifest OK: {len(assets)} assets, all filenames and alt text valid.\n")

    # --- optimise -----------------------------------------------------------
    payloads: dict[str, bytes] = {}
    print("Optimising:")
    for asset in assets:
        data, info = img.optimise(asset.source, hero=(asset.role == "hero"),
                                  quality=settings.get("quality", 82))
        payloads[asset.filename] = data
        asset.original_bytes = asset.source.stat().st_size
        asset.optimised_bytes = len(data)
        flag = "" if len(data) <= settings.get("size_budget_kb", 250) * 1024 \
            else "  OVER BUDGET"
        print(f"  {asset.filename[:52]:<54} "
              f"{asset.original_bytes/1024:7.0f} KB -> "
              f"{asset.optimised_bytes/1024:6.0f} KB "
              f"({asset.saving_pct:4.1f}% smaller, q{info['quality']}, "
              f"{info['width']}x{info['height']}){flag}")

    total_before = sum(a.original_bytes for a in assets) / 1024 / 1024
    total_after = sum(a.optimised_bytes for a in assets) / 1024 / 1024
    print(f"\n  total {total_before:.1f} MB -> {total_after:.1f} MB "
          f"({100*(1-total_after/total_before):.1f}% smaller)\n")

    targets = [a for a in assets if a.role in roles]
    print(f"Phase {phase} placement targets ({sorted(roles)}): "
          f"{len(targets)} of {len(assets)} assets\n")

    if not confirm:
        print("DRY RUN - nothing uploaded, nothing placed.")
        print("Re-run with --confirm to upload and place.\n")
        for asset in targets:
            print(f"  would place {asset.filename}")
            print(f"      on {asset.page} ({asset.role})")
            print(f"      alt: {asset.alt}")
        return {"uploaded": 0, "placed": 0, "assets": assets}

    # --- upload -------------------------------------------------------------
    print("Uploading:")
    uploaded = 0
    for asset in assets:
        existing = img.find_existing(client, asset.filename)
        if existing:
            asset.media_id = existing["id"]
            asset.url = existing["source_url"]
            asset.notes.append("already in media library; reused")
            print(f"  = {asset.filename[:56]:<58} reused id {existing['id']}")
            continue
        created = img.upload(client, asset, payloads[asset.filename])
        asset.media_id = created["id"]
        asset.url = created["source_url"]
        uploaded += 1
        print(f"  + {asset.filename[:56]:<58} id {created['id']}")

    # --- place --------------------------------------------------------------
    print("\nPlacing:")
    placed = 0

    if phase == 2:
        return _place_phase_2(client, config, manifest, assets, page_ids,
                              out_dir, uploaded)

    by_page: dict[str, list[img.Asset]] = {}
    for asset in targets:
        by_page.setdefault(asset.page, []).append(asset)

    for page_key, page_assets in by_page.items():
        spec = page_ids[page_key]
        slug = spec["match"]["slug"]
        pages = {p["slug"]: p for p in client.pages()}
        page = pages.get(slug)
        if page is None:
            print(f"  ! page {slug!r} not found; skipped")
            continue

        detail = client._request("GET", f"wp-json/wp/v2/pages/{page['id']}",
                                 params={"context": "edit"})
        raw = detail["content"]["raw"]
        before_slots = img.count_placeholder_slots(raw)

        for asset in page_assets:
            if asset.role != "hero":
                continue
            unique_id, method = hero_slot(raw)
            if unique_id is None:
                print(f"  = {page_key}: no placeholder hero slot remaining "
                      "(already placed); skipped")
                continue
            raw, changed = img.replace_slot(raw, unique_id, asset.url, asset.alt)
            if changed:
                placed += 1
                print(f"  + {page_key}: hero slot {unique_id} <- "
                      f"{asset.filename}  [{method}]")
            else:
                print(f"  = {page_key}: hero slot {unique_id} already repointed")

        after_slots = img.count_placeholder_slots(raw)
        if raw != detail["content"]["raw"]:
            # Back up the ORIGINAL block markup before overwriting it. The
            # page-level snapshots taken by `apply` cover titles and metadata
            # only; this command rewrites content, so it must protect content.
            from datetime import datetime, timezone
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = (out_dir.parent / "snapshots"
                      / f"content-{stamp}-{page['id']}.json")
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text(json.dumps({
                "taken": stamp,
                "page_id": page["id"],
                "slug": page["slug"],
                "content_raw": detail["content"]["raw"],
            }, indent=2), encoding="utf8")
            print(f"    backed up original content -> snapshots/{backup.name}")
            client.update_page_fields(page["id"], content=raw)
            print(f"    saved {page_key}: placeholder slots "
                  f"{before_slots} -> {after_slots}")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "image-manifest-result.json").write_text(json.dumps([
        {
            "filename": a.filename, "media_id": a.media_id, "url": a.url,
            "alt": a.alt, "role": a.role, "page": a.page,
            "original_bytes": a.original_bytes,
            "optimised_bytes": a.optimised_bytes,
            "saving_pct": round(a.saving_pct, 1),
            "notes": a.notes,
        } for a in assets
    ], indent=2), encoding="utf8")
    print(f"\nUploaded {uploaded}, placed {placed}. "
          "Wrote out/image-manifest-result.json")
    return {"uploaded": uploaded, "placed": placed, "assets": assets}


def _place_phase_2(client, config: dict, manifest: dict, assets: list,
                   page_ids: dict, out_dir: Path, uploaded: int) -> dict:
    """Place section images using the explicit slot map.

    Each slot is addressed by uniqueId and paired in config with the heading
    that follows it, so relevance is a recorded decision rather than an
    ordinal accident. Slots whose match is only topical are marked 'general'
    and reported, not presented as precise.
    """
    from datetime import datetime, timezone

    by_filename = {a.filename: a for a in assets}
    placements = manifest.get("placements") or {}
    placed = 0
    generic: list[dict] = []
    log: list[dict] = []

    pages = {p["slug"]: p for p in client.pages()}

    for page_key, entries in placements.items():
        spec = page_ids.get(page_key)
        if spec is None:
            print(f"  ! unknown page id {page_key!r} in placements; skipped")
            continue
        page = pages.get(spec["match"]["slug"])
        if page is None:
            print(f"  ! page {spec['match']['slug']!r} not found; skipped")
            continue

        detail = client._request("GET", f"wp-json/wp/v2/pages/{page['id']}",
                                 params={"context": "edit"})
        raw = original = detail["content"]["raw"]
        before = img.count_placeholder_slots(raw)
        print(f"\n  {page_key} ({page['id']}) - {len(entries)} slots")

        for entry in entries:
            asset = by_filename.get(entry["asset"])
            if asset is None or not asset.url:
                print(f"    ! asset {entry['asset']!r} not uploaded; skipped")
                continue
            raw, changed = img.ensure_slot(raw, entry["slot"], asset.url,
                                           asset.alt)
            mark = "+" if changed else "="
            rel = entry.get("relevance", "specific")
            print(f"    {mark} {entry['slot']:<10} {entry['heading'][:34]:<36} "
                  f"<- {entry['asset'][:44]}  [{rel}]")
            if changed:
                placed += 1
            if rel != "specific":
                generic.append({"page": page_key, "slot": entry["slot"],
                                "heading": entry["heading"],
                                "asset": entry["asset"]})
            log.append({"page": page_key, "slot": entry["slot"],
                        "heading": entry["heading"], "asset": entry["asset"],
                        "relevance": rel, "changed": changed})

        # CSS-background elements: no <img>, no alt, addressed separately.
        for entry in (manifest.get("backgrounds") or {}).get(page_key, []):
            asset = by_filename.get(entry["asset"])
            if asset is None or not asset.url:
                print(f"    ! background asset {entry['asset']!r} not uploaded")
                continue
            raw, changed = img.ensure_background_slot(raw, entry["slot"],
                                                      asset.url)
            mark = "+" if changed else "="
            print(f"    {mark} {entry['slot']:<10} {entry['heading'][:34]:<36} "
                  f"<- {entry['asset'][:44]}  [background]")
            if changed:
                placed += 1
            log.append({"page": page_key, "slot": entry["slot"],
                        "heading": entry["heading"], "asset": entry["asset"],
                        "relevance": "background", "changed": changed})

        if raw != original:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = out_dir.parent / "snapshots" / f"content-{stamp}-{page['id']}.json"
            backup.parent.mkdir(parents=True, exist_ok=True)
            backup.write_text(json.dumps({
                "taken": stamp, "page_id": page["id"], "slug": page["slug"],
                "content_raw": original,
            }, indent=2), encoding="utf8")
            client.update_page_fields(page["id"], content=raw)
            after = img.count_placeholder_slots(raw)
            print(f"      saved: placeholder image slots {before} -> {after} "
                  f"(backup snapshots/{backup.name})")

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "image-placement-log.json").write_text(
        json.dumps({"placed": placed, "entries": log,
                    "general_relevance": generic}, indent=2), encoding="utf8")

    print(f"\nUploaded {uploaded}, placed {placed}.")
    if generic:
        print(f"{len(generic)} placement(s) are topically relevant rather than "
              "pest-specific; recorded in out/image-placement-log.json:")
        for item in generic:
            print(f"   {item['page']:<18} {item['heading'][:30]:<32} "
                  f"{item['asset']}")
    return {"uploaded": uploaded, "placed": placed, "assets": assets}
