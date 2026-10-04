"""Fills template placeholders from approved sources.

Two sources feed a placeholder, and only two:

  1. the Site Variable Sheet, for a value whose status is Approved
  2. the approved content package (CONTENT_SOURCE, status Approved)

Anything a placeholder cannot draw from one of those is left exactly as found
and reported as a shortfall. A template slot with no approved content is not a
gap to be filled with something plausible -- it is a finding.
"""
from __future__ import annotations

import html
import re
from dataclasses import dataclass, field

from .content import PageContent
from .sheet import VariableSet

PLACEHOLDER_RE = re.compile(r"\{\{([A-Z0-9_]+)\}\}")

# Placeholders answered by the variable sheet rather than the content package.
VARIABLE_BACKED = {
    "BRAND_NAME": "{{BRAND_NAME}}",
    "CORE_SERVICES": "{{CORE_SERVICES}}",
    "PRIMARY_SERVICE": "{{PRIMARY_SERVICE}}",
    "CTA_HEADING": "{{CTA_HEADING}}",
    "CTA_DESCRIPTION": "{{CTA_DESCRIPTION}}",
    "SERVICE_AREA_LIST": "{{SERVICE_AREA_LIST}}",
    "STATE": "{{STATE}}",
    "STATE_ABBREVIATION": "{{STATE_ABBREVIATION}}",
    "NICHE": "{{NICHE}}",
}

# Page-specific variable overrides.
PAGE_VARIABLE_BACKED = {
    "location_lakeside": {"SERVICE_AREA": "{{SERVICE_AREA}}"},
}

# Placeholders that repeat and draw one item per slot from a single Approved
# list variable. {{TRUST_POINTS}} is supplied as a bulleted block; each bullet
# is one approved trust point, so the list is split rather than pasted whole.
LIST_BACKED = {
    "TRUST_POINT": "{{TRUST_POINTS}}",
}


# Placeholders answered by TRANSFORMING an Approved value rather than using it
# verbatim. Every transform is a documented, reversible text operation on an
# Approved source — never a new fact. A slot filled this way is recorded as
# "derived" in the fill report so a reviewer can check the derivation.
#
# {{SERVICE_CATEGORY}} is not supplied in the site variable sheet, but the
# templates use it as a category label inside a sentence: "Our Commercial
# {{SERVICE_CATEGORY}} Services", "{{SERVICE_CATEGORY}} FAQs". Pasting
# {{PRIMARY_SERVICE}} verbatim gives "Our Commercial Pest control services
# Services". Dropping the trailing noun and title-casing gives "Pest Control",
# which reads correctly and introduces no word that is not in the source.
DERIVED = {
    "SERVICE_CATEGORY": ("{{PRIMARY_SERVICE}}", "category_label"),
}

_TRAILING_NOUNS = ("services", "service")


def derive_category_label(value: str) -> str:
    """'Pest control services' -> 'Pest Control'. Words only ever removed."""
    words = value.strip().split()
    while words and words[-1].lower() in _TRAILING_NOUNS:
        words.pop()
    return " ".join(w.capitalize() if w.islower() else w for w in words)


TRANSFORMS = {"category_label": derive_category_label}


def split_list_value(value: str) -> list[str]:
    """Split an approved multi-line or semicolon-delimited list variable."""
    if not value:
        return []
    parts = re.split(r"[\r\n]+|;", value)
    cleaned = [re.sub(r"^[-–•*]\s*", "", p).strip() for p in parts]
    return [p for p in cleaned if p]

# The templates express each subpage H1 as a formula rather than a field.
# The sheet supplies an approved page name for each, so the whole formula is
# replaced with that single approved value instead of guessing at its parts.
H1_FORMULA = "{{CORE_SERVICE}} + {{SERVICE_AREA}}"
PAGE_NAME_VARIABLE = {
    "about": "ABOUT_PAGE_NAME",
    "service_termite": "SERVICE_PAGE_NAME",
    "location_lakeside": "LOCATION_PAGE_NAME",
}


@dataclass
class Shortfall:
    page: str
    placeholder: str
    demand: int
    supply: int
    source: str

    @property
    def missing(self) -> int:
        return max(0, self.demand - self.supply)


@dataclass
class FillResult:
    page: str
    applied: int = 0
    remaining: int = 0
    shortfalls: list[Shortfall] = field(default_factory=list)
    filled_keys: dict[str, int] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


def _escape(value: str) -> str:
    """Escape for insertion into block markup.

    Content is written into both a JSON block comment and rendered HTML, so a
    stray & or < would corrupt one or the other. Curly quotes and em dashes in
    the source are preserved -- they are part of the approved copy.
    """
    return html.escape(value, quote=False)


def fill_page(raw: str, page_key: str, content: PageContent,
              variables: VariableSet, h1_variable: str | None = None
              ) -> tuple[str, FillResult]:
    result = FillResult(page=page_key)
    result.notes.extend(content.notes)

    # 0. An Approved page H1 overrides whatever formula the template carries.
    if h1_variable:
        approved_h1 = variables.approved(h1_variable)
        raw, changed_h1 = set_page_h1(raw, approved_h1 or "")
        if changed_h1:
            result.applied += 1
            result.filled_keys[f"H1 (from {h1_variable})"] = 1

    # 1. The H1 formula, replaced as a whole from the approved page name.
    name_var = PAGE_NAME_VARIABLE.get(page_key)
    if name_var and H1_FORMULA in raw:
        approved_name = variables.approved(name_var)
        if approved_name:
            occurrences = raw.count(H1_FORMULA)
            raw = raw.replace(H1_FORMULA, _escape(approved_name))
            result.applied += occurrences
            result.filled_keys["H1 (page name)"] = occurrences

    # 2. Measure demand before consuming anything, so shortfalls are reported
    #    against the real slot count rather than whatever is left mid-pass.
    demand: dict[str, int] = {}
    for key in PLACEHOLDER_RE.findall(raw):
        demand[key] = demand.get(key, 0) + 1

    variable_map = dict(VARIABLE_BACKED)
    variable_map.update(PAGE_VARIABLE_BACKED.get(page_key, {}))

    queues = {key: list(items) for key, items in content.queues.items()}

    # Drop content that is already on the page.
    #
    # Without this, a second run re-consumes each queue from the start and
    # fills the SURPLUS slots with duplicates of the items it placed the first
    # time -- nine services becoming eighteen cards listing the same nine.
    # Skipping items already present makes the pass idempotent and keeps a
    # partially-filled template partially filled rather than padded out.
    stripped = re.sub(r"<!--.*?-->", "", raw, flags=re.S)
    for key, items in queues.items():
        queues[key] = [
            item for item in items
            if _escape(item)[:80] not in stripped and item[:80] not in stripped
        ]

    # A list-backed placeholder prefers the Approved sheet variable. It falls
    # back to the content package only when the sheet supplies nothing, so the
    # sheet stays the single source of truth where it has an answer.
    for key, variable_key in LIST_BACKED.items():
        items = split_list_value(variables.approved(variable_key) or "")
        if items:
            queues[key] = items

    for key, count in sorted(demand.items()):
        if key in variable_map:
            value = variables.approved(variable_map[key])
            supply = count if value else 0
            source = f"variable sheet ({variable_map[key]})"
        elif key in DERIVED and not queues.get(key):
            source_key, transform = DERIVED[key]
            supply = count if variables.approved(source_key) else 0
            source = f"derived from {source_key} via {transform}"
        elif key in LIST_BACKED and queues.get(key):
            supply = min(count, len(queues[key]))
            source = f"variable sheet ({LIST_BACKED[key]}, one item per slot)"
        else:
            supply = min(count, len(queues.get(key, [])))
            source = "content package"
        if supply < count:
            result.shortfalls.append(
                Shortfall(page_key, key, count, supply, source)
            )

    # 3. Consume in document order.
    def substitute(match: re.Match) -> str:
        key = match.group(1)
        if key in variable_map:
            value = variables.approved(variable_map[key])
            if not value:
                return match.group(0)
        else:
            queue = queues.get(key)
            if queue:
                value = queue.pop(0)
            elif key in DERIVED:
                # Content package is exhausted; fall back to a documented
                # transform of an Approved value. Recorded separately so the
                # derivation is visible rather than indistinguishable from
                # supplied copy.
                source_key, transform = DERIVED[key]
                approved = variables.approved(source_key)
                if not approved:
                    return match.group(0)
                value = TRANSFORMS[transform](approved)
                result.filled_keys[f"{key} (derived)"] = (
                    result.filled_keys.get(f"{key} (derived)", 0) + 1)
                result.applied += 1
                return _escape(value)
            else:
                return match.group(0)
        result.applied += 1
        result.filled_keys[key] = result.filled_keys.get(key, 0) + 1
        return _escape(value)

    filled = PLACEHOLDER_RE.sub(substitute, raw)
    result.remaining = len(PLACEHOLDER_RE.findall(filled))
    return filled, result


CORE_H1_RE = re.compile(
    r'(<!--\s+wp:heading\s+\{[^\n]*?"level":1[^\n]*?-->\s*<h1[^>]*>)(.*?)(</h1>)',
    re.S,
)


def set_page_h1(raw: str, approved_h1: str) -> tuple[str, bool]:
    """Set a page's hero H1 from an Approved value.

    The homepage hero H1 is a core/heading block, not a GenerateBlocks text
    block, so the placeholder pass treated its contents as ordinary copy and
    satisfied the template formula "{{BRAND_NAME}} | {{CORE_SERVICES}}"
    literally -- producing a 154-character H1 listing every service, while the
    sheet's Approved HOMEPAGE_H1 went unused.

    An approved value that exists and is not applied is as much a failure as
    an unapproved value that is.

    Idempotent: returns changed=False when the H1 already matches.
    """
    if not approved_h1:
        return raw, False

    match = CORE_H1_RE.search(raw)
    if match is None:
        return raw, False

    current = re.sub(r"<[^>]+>", "", match.group(2)).strip()
    if current == approved_h1.strip():
        return raw, False

    updated = match.group(1) + _escape(approved_h1) + match.group(3)
    return raw[: match.start()] + updated + raw[match.end():], True
