"""Image optimisation, upload and placement.

Adding images is optional under the QA checklist, but the moment an image IS
added it must meet relevance, optimisation, filename and alt-text
requirements. So this module does all four or none: nothing is uploaded
without a descriptive filename and alt text, and nothing is placed into a page
without a recorded reason for that placement.

Alt text is supplied by the operator in config/images.yml, not generated here.
It describes what is visibly in the frame and asserts no business fact -- no
company name, no service claim, no location claim -- because none of those are
supplied or approved in the Site Variable Sheet.
"""
from __future__ import annotations

import io
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image

MAX_WIDTH = 1600
HERO_MAX_WIDTH = 2000
WEBP_QUALITY = 82
SIZE_BUDGET_KB = 250


@dataclass
class Asset:
    source: Path
    filename: str          # descriptive, hyphenated, no spaces
    alt: str
    role: str              # hero | section | cta | detail
    page: str              # config page id this asset belongs to
    media_id: int | None = None
    url: str | None = None
    optimised_bytes: int = 0
    original_bytes: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def saving_pct(self) -> float:
        if not self.original_bytes:
            return 0.0
        return 100 * (1 - self.optimised_bytes / self.original_bytes)


FILENAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*\.webp$")


def validate_filename(name: str) -> str | None:
    if not FILENAME_RE.match(name):
        return (f"{name!r} is not a lowercase hyphenated .webp filename")
    if len(name) > 80:
        return f"{name!r} is longer than 80 characters"
    return None


def validate_alt(alt: str) -> str | None:
    if not alt or not alt.strip():
        return "alt text is empty"
    if len(alt) > 160:
        return f"alt text is {len(alt)} characters (keep under 160)"
    if alt.lower().startswith(("image of", "picture of", "photo of")):
        return "alt text should describe the content, not restate that it is an image"
    return None


def optimise(source: Path, *, hero: bool = False,
             quality: int = WEBP_QUALITY) -> tuple[bytes, dict]:
    """Downscale and re-encode to WebP, stepping quality down to hit budget."""
    with Image.open(source) as im:
        im = im.convert("RGB")
        max_width = HERO_MAX_WIDTH if hero else MAX_WIDTH
        if im.width > max_width:
            ratio = max_width / im.width
            im = im.resize((max_width, round(im.height * ratio)),
                           Image.Resampling.LANCZOS)

        data = b""
        used_quality = quality
        for attempt_quality in range(quality, 55, -6):
            buffer = io.BytesIO()
            im.save(buffer, format="WEBP", quality=attempt_quality, method=6)
            data = buffer.getvalue()
            used_quality = attempt_quality
            if len(data) <= SIZE_BUDGET_KB * 1024:
                break

        return data, {
            "width": im.width,
            "height": im.height,
            "quality": used_quality,
            "bytes": len(data),
        }


def upload(client, asset: Asset, data: bytes) -> dict:
    """Upload bytes to the media library, then set alt text.

    WordPress ignores alt_text on the create call, so it is set with a follow-up
    update and then read back. An upload whose alt text did not stick is a
    failed upload, not a successful one.
    """
    response = client._session.post(
        client._url("wp-json/wp/v2/media"),
        data=data,
        headers={
            "Content-Type": "image/webp",
            "Content-Disposition": f'attachment; filename="{asset.filename}"',
        },
        timeout=120,
    )
    if response.status_code >= 400:
        raise RuntimeError(
            f"upload failed for {asset.filename}: HTTP {response.status_code} "
            f"{response.text[:200]}"
        )
    created = response.json()

    updated = client._request(
        "POST", f"wp-json/wp/v2/media/{created['id']}",
        json={"alt_text": asset.alt, "title": asset.alt[:100]},
    )
    if (updated.get("alt_text") or "").strip() != asset.alt.strip():
        raise RuntimeError(
            f"alt text did not persist for {asset.filename}; "
            "refusing to report this upload as successful"
        )
    return updated


def find_existing(client, filename: str) -> dict | None:
    """Return an already-uploaded asset with this filename, if any.

    Makes the image pass idempotent: a second run reuses the existing
    attachment instead of creating filename-2.webp duplicates.
    """
    stem = filename.rsplit(".", 1)[0]
    results = client._request(
        "GET", "wp-json/wp/v2/media",
        params={"search": stem, "per_page": 100,
                "_fields": "id,source_url,alt_text,slug"},
    )
    for item in results or []:
        if item["source_url"].rsplit("/", 1)[-1].startswith(stem):
            return item
    return None


PLACEHOLDER_HOST = "patterns.generatepress.com"


def count_placeholder_slots(raw: str) -> int:
    return len(re.findall(r'"src":"https://' + re.escape(PLACEHOLDER_HOST)
                          + r'[^"]*"', raw))


def _serialise_block_attrs(attrs: dict) -> str:
    """Serialise block attributes the way WordPress does.

    Two conventions matter. Slashes are not escaped, and a literal '--' is
    written as \\u002d\\u002d because these attributes live inside an HTML
    comment, where '--' terminates parsing in some consumers. Getting this
    wrong produces markup that renders but breaks in the block editor.
    """
    text = json.dumps(attrs, ensure_ascii=False, separators=(",", ":"))
    return text.replace("--", "\\u002d\\u002d")


def _set_img_attr(tag: str, name: str, value: str) -> str:
    """Set an attribute on an <img> tag, inserting it if absent."""
    pattern = re.compile(r'\s' + re.escape(name) + r'="[^"]*"')
    replacement = f' {name}="{value}"'
    if pattern.search(tag):
        return pattern.sub(replacement, tag, count=1)
    return tag[:-1].rstrip() + replacement + ">"


def ensure_slot(raw: str, unique_id: str, new_url: str,
                alt: str) -> tuple[str, bool]:
    """Point one generateblocks/media block at an image, with alt text.

    Rewrites the block-comment JSON structurally rather than by string
    substitution. The earlier string-replace approach assumed every block
    carried an empty alt="" to overwrite; blocks that simply had no alt key at
    all were silently left without alt text while the run reported success.

    Idempotent: running it again when src and alt already match is a no-op.
    """
    block_re = re.compile(
        r'<!-- wp:generateblocks/media (\{.*?\}) -->(\s*)(<img[^>]*>)',
        re.S,
    )

    for match in block_re.finditer(raw):
        try:
            attrs = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if attrs.get("uniqueId") != unique_id:
            continue

        html_attrs = attrs.setdefault("htmlAttributes", {})
        already = (html_attrs.get("src") == new_url
                   and html_attrs.get("alt") == alt)
        if already:
            return raw, False

        html_attrs["src"] = new_url
        html_attrs["alt"] = alt
        # The template leaves behind titles like "Sample" or a stray source
        # filename ("magai-020325120259"). Neither describes the new image.
        html_attrs["title"] = alt

        tag = match.group(3)
        tag = _set_img_attr(tag, "src", new_url)
        tag = _set_img_attr(tag, "alt", alt)
        tag = _set_img_attr(tag, "title", alt)

        rebuilt = (
            "<!-- wp:generateblocks/media "
            + _serialise_block_attrs(attrs)
            + " -->" + match.group(2) + tag
        )
        return raw[: match.start()] + rebuilt + raw[match.end():], True

    return raw, False


# Retained name for callers; behaviour is now "ensure", not blind replace.
replace_slot = ensure_slot


def ensure_background_slot(raw: str, unique_id: str,
                           new_url: str) -> tuple[str, bool]:
    """Repoint a generateblocks/element that uses an image as a CSS background.

    These are not media blocks and carry no <img> tag, so the media-block
    replacement never sees them -- which is how four CTA band backgrounds
    stayed hot-linked to a third-party CDN while every visible <img> had been
    replaced. The URL appears twice inside the block attributes: once in
    styles.backgroundImage and once in the generated css rule. Both must move
    together.

    No alt text is set: a CSS background is decorative and has no alt.
    """
    block_re = re.compile(
        r'<!-- wp:generateblocks/element (\{.*?\}) -->', re.S
    )
    for match in block_re.finditer(raw):
        try:
            attrs = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if attrs.get("uniqueId") != unique_id:
            continue

        blob = json.dumps(attrs)
        if PLACEHOLDER_HOST not in blob:
            return raw, False  # already repointed

        updated_blob = re.sub(
            r'https://' + re.escape(PLACEHOLDER_HOST) + r'[^"\')]*',
            new_url, blob,
        )
        updated_attrs = json.loads(updated_blob)
        rebuilt = ("<!-- wp:generateblocks/element "
                   + _serialise_block_attrs(updated_attrs) + " -->")
        return raw[: match.start()] + rebuilt + raw[match.end():], True

    return raw, False
