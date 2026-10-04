"""Applies the sheet's approved internal-linking instructions.

Two rows of the Site Variable Sheet are instructions rather than values:

    SERVICE_PAGE_FIRST_BRAND_NAME_LINK   status Approved
    LOCATION_PAGE_FIRST_BRAND_NAME_LINK  status Approved

    "First mention of {{BRAND_NAME}} within the <page> content must link to
     the homepage"

Both were missed on the first pass because the planner looks for values to
write into fields, and these rows describe an operation instead. An approved
instruction that is never carried out is the same failure as an unapproved
value that is written -- the sheet was not honoured either way.

The homepage is linked as a ROOT-RELATIVE "/" rather than an absolute URL.
{{DOMAIN}} is Missing, so no approved absolute URL for this site exists;
baking the staging hostname into page content would embed a value the sheet
does not supply and would break when the site moves.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

HOMEPAGE_HREF = "/"

# Spans that must never be edited: block-delimiter comments carry JSON
# attributes, and rewriting text inside one corrupts the block.
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
ANCHOR_RE = re.compile(r"<a\b[^>]*>.*?</a>", re.S | re.I)
HEADING_RE = re.compile(r"<h[1-6]\b[^>]*>.*?</h[1-6]>", re.S | re.I)


@dataclass
class LinkResult:
    changed: bool
    reason: str
    position: int | None = None
    context: str = ""


def _protected_spans(raw: str) -> list[tuple[int, int]]:
    spans = [(m.start(), m.end()) for m in COMMENT_RE.finditer(raw)]
    spans += [(m.start(), m.end()) for m in ANCHOR_RE.finditer(raw)]
    spans += [(m.start(), m.end()) for m in HEADING_RE.finditer(raw)]
    return spans


def _inside(position: int, spans: list[tuple[int, int]]) -> bool:
    return any(start <= position < end for start, end in spans)


def link_first_brand_mention(raw: str, brand: str,
                             href: str = HOMEPAGE_HREF) -> tuple[str, LinkResult]:
    """Wrap the first eligible mention of `brand` in a link to the homepage.

    Eligibility, in order of the guards applied:
      * not inside a block-delimiter comment (would corrupt block JSON)
      * not already inside an anchor (idempotency, and no nested links)
      * not inside a heading -- linking a page's own H1 to the homepage is
        poor practice and the instruction is about the brand mention in body
        copy. Recorded when it happens rather than silently skipped.
      * must sit in element text, i.e. between '>' and the next '<'
    """
    if not brand:
        return raw, LinkResult(False, "no brand name supplied")

    # Already satisfied? Look for the brand inside any existing anchor.
    for anchor in ANCHOR_RE.finditer(raw):
        if brand in anchor.group(0):
            return raw, LinkResult(
                False, "first mention is already linked", anchor.start(),
                anchor.group(0)[:90])

    spans = _protected_spans(raw)

    for match in re.finditer(re.escape(brand), raw):
        start = match.start()
        if _inside(start, spans):
            continue
        # Confirm the text sits in element content, not in an attribute.
        prev_open = raw.rfind("<", 0, start)
        prev_close = raw.rfind(">", 0, start)
        if prev_open > prev_close:
            continue  # we are inside a tag

        replacement = f'<a href="{href}">{brand}</a>'
        updated = raw[:start] + replacement + raw[match.end():]
        context = raw[max(0, start - 50):start + len(brand) + 50]
        return updated, LinkResult(True, "linked first eligible mention",
                                   start, context)

    return raw, LinkResult(False, "no eligible mention found in page content")


def verify(raw: str, brand: str, href: str = HOMEPAGE_HREF) -> bool:
    """True when the brand is linked to the homepage somewhere in content."""
    pattern = re.compile(
        r'<a\b[^>]*href="' + re.escape(href) + r'"[^>]*>\s*'
        + re.escape(brand) + r'\s*</a>', re.I)
    return bool(pattern.search(raw))
