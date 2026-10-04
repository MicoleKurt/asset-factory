"""Removes template blocks that hold no approved content.

This is the one operation in the workflow that DELETES rather than fills, so
it carries the strictest guards in the codebase:

  1. A block is only removable when its visible text is entirely {{PLACEHOLDER}}
     tokens and whitespace. One character of real content and it stays.
  2. Block-comment balance is checked before and after. If the open/close count
     does not match, the edit is discarded rather than saved.
  3. It is opt-in (`--prune`), dry-run by default, and snapshots first.

Why remove at all: the templates repeat a service group twice — 17 card slots —
while the approved content package supplies one list of 9 services. The surplus
cards can only be filled by inventing service names and descriptions, which is
precisely what the assessment prohibits. Shipping eight cards reading
{{SERVICE_NAME}} is the other failure. Removing an empty repeater instance
invents nothing and is reversible from the snapshot.

Note this removes *instances* of a repeated card, not the pattern itself. The
pattern remains in the site's pattern library, untouched.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

BLOCK_OPEN = re.compile(r"<!--\s+wp:([a-z0-9/-]+)(\s+\{.*?\})?\s*(/)?-->", re.S)
BLOCK_CLOSE = re.compile(r"<!--\s+/wp:([a-z0-9/-]+)\s+-->")
PLACEHOLDER = re.compile(r"\{\{[A-Z0-9_]+\}\}")
ANY_BLOCK = re.compile(r"<!--\s+(/?)wp:([a-z0-9/-]+)(\s+\{.*?\})?\s*(/)?-->", re.S)


@dataclass
class Removal:
    unique_id: str
    block: str
    start: int
    end: int
    text: str
    placeholders: list[str]

    @property
    def length(self) -> int:
        return self.end - self.start


def visible_text(markup: str) -> str:
    """Text a visitor would see: block comments and tags stripped."""
    without_comments = re.sub(r"<!--.*?-->", "", markup, flags=re.S)
    without_tags = re.sub(r"<[^>]+>", " ", without_comments)
    return re.sub(r"\s+", " ", without_tags).strip()


def is_empty_of_content(markup: str) -> bool:
    """True when every scrap of visible text is an unresolved placeholder."""
    text = visible_text(markup)
    if not text:
        return False  # nothing to judge; leave it alone
    if not PLACEHOLDER.search(text):
        return False
    remainder = PLACEHOLDER.sub("", text)
    # Punctuation and symbols around a placeholder are decoration, not
    # content. The testimonial card renders its quote wrapped in curly
    # quotation marks, and a hand-written character class kept missing
    # them, so Unicode categories are used instead of enumerating marks.
    #
    # Digits are deliberately NOT stripped: process-step cards contain a
    # bare "1", "2", "3" and must survive.
    remainder = "".join(
        ch for ch in remainder
        if not ch.isspace()
        and not unicodedata.category(ch).startswith(("P", "S"))
    )
    return remainder == ""


def find_block_span(raw: str, unique_id: str) -> tuple[int, int] | None:
    """Byte span of the block with this uniqueId, including its close comment."""
    anchor = re.search(
        r'<!--\s+wp:[a-z0-9/-]+\s+\{[^\n]*?"uniqueId":"' + re.escape(unique_id) + r'"',
        raw,
    )
    if anchor is None:
        return None
    start = anchor.start()

    opening = ANY_BLOCK.match(raw, start)
    if opening is None:
        return None
    if opening.group(4):  # self-closing
        return start, opening.end()

    depth = 0
    for token in ANY_BLOCK.finditer(raw, start):
        closing, self_closing = token.group(1), token.group(4)
        if self_closing:
            continue
        if closing:
            depth -= 1
            if depth == 0:
                return start, token.end()
        else:
            depth += 1
    return None


def balanced(raw: str) -> bool:
    depth = 0
    for token in ANY_BLOCK.finditer(raw):
        if token.group(4):
            continue
        depth += -1 if token.group(1) else 1
        if depth < 0:
            return False
    return depth == 0


def find_removable(raw: str, container_ids: list[str] | None = None
                   ) -> list[Removal]:
    """Blocks eligible for removal, outermost first, non-overlapping.

    When container_ids is given, only those blocks are considered — and each
    is still subjected to the emptiness test. Naming a block in config does
    not override the guard.
    """
    candidates: list[Removal] = []

    if container_ids:
        for unique_id in container_ids:
            span = find_block_span(raw, unique_id)
            if span is None:
                continue
            markup = raw[span[0]:span[1]]
            if not is_empty_of_content(markup):
                continue
            candidates.append(Removal(
                unique_id=unique_id, block="configured", start=span[0],
                end=span[1], text=visible_text(markup),
                placeholders=sorted(set(PLACEHOLDER.findall(markup))),
            ))
    else:
        seen: set[str] = set()
        for match in re.finditer(r'"uniqueId":"([a-z0-9]+)"', raw):
            unique_id = match.group(1)
            if unique_id in seen:
                continue
            seen.add(unique_id)
            span = find_block_span(raw, unique_id)
            if span is None:
                continue
            markup = raw[span[0]:span[1]]
            if not is_empty_of_content(markup):
                continue
            candidates.append(Removal(
                unique_id=unique_id, block="auto", start=span[0], end=span[1],
                text=visible_text(markup),
                placeholders=sorted(set(PLACEHOLDER.findall(markup))),
            ))

    # Keep only outermost spans, so a parent and its children are not both
    # removed (which would corrupt offsets).
    candidates.sort(key=lambda r: (r.start, -r.length))
    kept: list[Removal] = []
    for candidate in candidates:
        if any(candidate.start >= k.start and candidate.end <= k.end
               for k in kept):
            continue
        kept.append(candidate)
    return kept


def find_orphan_sections(raw: str, section_ids: list[str],
                         expect_text: dict[str, str],
                         max_text: int = 120) -> list[Removal]:
    """Named wrappers left holding only their heading after stage 1.

    Deliberately NOT automatic. An earlier version scanned for any block with
    little text and lots of markup, and proposed deleting the testimonials,
    the FAQ accordions and the service-area list -- every one of which is
    short text inside heavy markup. Heuristics cannot tell "a heading over
    nothing" from "an accordion item"; only the operator can.

    So a section must be named in config AND still satisfy three guards:
      * its remaining text matches what config says it should be
      * that text is short enough to be a lone heading
      * it contains no image and no surviving placeholder
    """
    orphans: list[Removal] = []
    for unique_id in section_ids:
        span = find_block_span(raw, unique_id)
        if span is None:
            continue
        markup = raw[span[0]:span[1]]
        text = visible_text(markup)
        expected = expect_text.get(unique_id, "")

        if expected and expected.lower() not in text.lower():
            continue  # not the block config described; refuse
        if len(text) > max_text or not text:
            continue
        if PLACEHOLDER.search(text) or "<img" in markup:
            continue

        orphans.append(Removal(
            unique_id=unique_id, block="orphan-section", start=span[0],
            end=span[1], text=text, placeholders=[],
        ))
    return orphans


def apply_removals(raw: str, removals: list[Removal]) -> tuple[str, bool]:
    """Remove spans back-to-front. Returns (markup, ok)."""
    if not removals:
        return raw, True
    updated = raw
    for removal in sorted(removals, key=lambda r: r.start, reverse=True):
        updated = updated[:removal.start] + updated[removal.end:]
    # Refuse to hand back markup whose block comments no longer balance.
    return (updated, True) if balanced(updated) else (raw, False)


def find_duplicate_sections(raw: str, section_ids: list[str],
                            expect_text: dict[str, str]) -> list[Removal]:
    """Named blocks that repeat a block already present earlier in the page.

    Removing the surplus service group orphaned its CTA banner, leaving two
    byte-identical "Request a Quote" bands four bytes apart. That banner holds
    real approved text, so the placeholder-only guard cannot see it and the
    orphan-heading guard should not: it is not an empty heading, it is a
    duplicate.

    The guard here is an invariant rather than a heuristic: a block is only
    removable when another block earlier in the document has the SAME visible
    text. That makes it structurally impossible to delete a unique section --
    if there is no earlier twin, there is nothing to remove.
    """
    removals: list[Removal] = []
    for unique_id in section_ids:
        span = find_block_span(raw, unique_id)
        if span is None:
            continue
        markup = raw[span[0]:span[1]]
        text = visible_text(markup)
        expected = expect_text.get(unique_id, "")

        if expected and expected.lower() not in text.lower():
            continue
        if not text:
            continue

        # Require an earlier block with identical visible text.
        twin = None
        for match in re.finditer(r'"uniqueId":"([a-z0-9]+)"', raw):
            other_id = match.group(1)
            if other_id == unique_id:
                continue
            other_span = find_block_span(raw, other_id)
            if other_span is None or other_span[0] >= span[0]:
                continue
            if other_span[1] > span[0]:
                continue  # overlapping / ancestor, not a sibling twin
            if visible_text(raw[other_span[0]:other_span[1]]) == text:
                twin = other_id
                break
        if twin is None:
            continue

        removals.append(Removal(
            unique_id=unique_id, block=f"duplicate-of-{twin}", start=span[0],
            end=span[1], text=text, placeholders=[],
        ))
    return removals
