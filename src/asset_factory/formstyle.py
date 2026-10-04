"""Extends the Fluent Forms block's own custom CSS to cover its success message.

The confirmation message that replaces the form after a submission renders in
dark text on a dark hero, and is effectively unreadable. That is a legibility
defect rather than a design preference: a person who submits the form cannot
read the confirmation.

The fix is deliberately the narrowest one available, and it is not a new design
decision:

  * The Fluent Forms Gutenberg block stores a `customCss` attribute in the PAGE
    CONTENT. The template author already wrote rules there and already set this
    form's labels to #ffffff. The success message is simply not covered by any
    of their selectors.
  * So this appends one rule, to that same attribute, using the same colour the
    template author already chose, scoped to the same form.
  * No Global Style is authored, no theme setting is touched, and no colour is
    invented. Removing the appended block restores the original exactly.

Idempotent: the rule is wrapped in sentinel comments and replaced rather than
duplicated on a second run.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

BLOCK_RE = re.compile(r"<!-- wp:fluentfom/guten-block (\{.*?\}) (/?)-->", re.S)

BEGIN = "/* asset-factory: success message legibility */"
END = "/* end asset-factory */"

# Fluent Forms renders the confirmation into .ff-message-success (6.x) and
# .ff_submit_success (older markup). Both are targeted so the rule does not
# depend on which one the installed version emits.
SUCCESS_SELECTORS = (
    ".ff-message-success",
    ".ff_submit_success",
    ".ff-message-success p",
    ".ff_submit_success p",
)


@dataclass
class StyleResult:
    changed: bool
    reason: str
    colour: str = ""


def _form_scope(css: str) -> str | None:
    """Recover the form-specific selector prefix the template already uses.

    Reusing the author's own scope keeps the new rule as tightly bound as
    their existing ones, rather than leaking to any other form on the site.
    """
    match = re.search(r"(\.fluentform[^\s{,]*ff_guten_block-\d+)", css)
    return match.group(1) if match else None


def _label_colour(css: str) -> str | None:
    """The colour the template author already applied to this form's labels."""
    match = re.search(
        r"ff-el-input(?:\\u002d\\u002d|--)label\s+label\s*\{[^}]*color:\s*"
        r"(#[0-9a-fA-F]{3,8}|rgba?\([^)]*\))",
        css,
    )
    return match.group(1) if match else None


def build_rule(css: str) -> tuple[str, str] | None:
    """Return (rule_text, colour) or None when it cannot be derived safely."""
    scope = _form_scope(css)
    colour = _label_colour(css)
    if not scope or not colour:
        return None
    selectors = ",\n".join(f"{scope} {s}" for s in SUCCESS_SELECTORS)
    rule = f"\n{BEGIN}\n{selectors} {{ color: {colour}; }}\n{END}\n"
    return rule, colour


def apply_to_content(raw: str) -> tuple[str, StyleResult]:
    """Append the success-message rule to every Fluent Forms block on a page."""
    match = BLOCK_RE.search(raw)
    if match is None:
        return raw, StyleResult(False, "no Fluent Forms block on this page")

    try:
        attrs = json.loads(match.group(1))
    except json.JSONDecodeError:
        return raw, StyleResult(False, "block attributes are not valid JSON")

    css = attrs.get("customCss") or ""
    # The attribute is a JSON-encoded string inside the block JSON.
    if css.startswith('"'):
        try:
            css = json.loads(css)
        except json.JSONDecodeError:
            pass

    built = build_rule(css)
    if built is None:
        return raw, StyleResult(
            False,
            "could not derive the form scope or the author's label colour; "
            "refusing to guess a selector or a colour",
        )
    rule, colour = built

    # Idempotent: replace any previous insertion rather than stacking copies.
    existing = re.search(
        re.escape(BEGIN) + r".*?" + re.escape(END), css, re.S)
    if existing:
        if existing.group(0).strip() == rule.strip():
            return raw, StyleResult(False, "rule already present", colour)
        css = css[: existing.start()] + rule.strip() + css[existing.end():]
    else:
        css = css.rstrip() + rule

    attrs["customCss"] = json.dumps(css)
    serialised = json.dumps(attrs, ensure_ascii=False, separators=(",", ":"))
    serialised = serialised.replace("--", "\\u002d\\u002d")
    replacement = f"<!-- wp:fluentfom/guten-block {serialised} {match.group(2)}-->"
    updated = raw[: match.start()] + replacement + raw[match.end():]
    return updated, StyleResult(True, "success message rule added", colour)


def verify(raw: str) -> bool:
    """True when the page content carries the appended rule."""
    return BEGIN in raw and "ff-message-success" in raw
