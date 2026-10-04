"""Guards on the Fluent Forms success-message legibility rule."""
from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from asset_factory.formstyle import (  # noqa: E402
    BEGIN,
    apply_to_content,
    build_rule,
    verify,
)

SCOPE = ".fluentform.fluentform_wrapper_3.ff_guten_block.ff_guten_block-3"
AUTHOR_CSS = (
    f"{SCOPE} .ff-el-input--label label {{ color: #ffffff; }}\n"
    f"{SCOPE} .ff-el-input--content select {{ padding: 5px; }}"
)


def _page(css: str = AUTHOR_CSS) -> str:
    attrs = {"formId": "3", "themeStyle": "ffs_inherit_theme",
             "customCss": json.dumps(css)}
    return ("<div><!-- wp:fluentfom/guten-block "
            + json.dumps(attrs, separators=(",", ":")) + " /--></div>")


def test_uses_the_colour_the_template_author_already_chose():
    """No colour is invented: it is read back from the author's own rule."""
    rule, colour = build_rule(AUTHOR_CSS)
    assert colour == "#ffffff"
    assert "color: #ffffff" in rule


def test_uses_the_authors_own_form_scope():
    """The rule must be bound as tightly as the author's existing ones."""
    rule, _ = build_rule(AUTHOR_CSS)
    assert rule.count(SCOPE) == 4
    assert ".ff-message-success" in rule


def test_refuses_when_no_author_colour_exists():
    """Without a colour to copy, guessing one is not acceptable."""
    assert build_rule(f"{SCOPE} .ff-el-input--content select "
                      "{ padding: 5px; }") is None


def test_refuses_when_scope_cannot_be_recovered():
    assert build_rule(".something-else label { color: #fff; }") is None


def test_applies_and_verifies():
    out, result = apply_to_content(_page())
    assert result.changed
    assert verify(out)
    assert result.colour == "#ffffff"


def test_is_idempotent():
    once, _ = apply_to_content(_page())
    twice, result = apply_to_content(once)
    assert twice == once
    assert not result.changed
    assert once.count(BEGIN) == 1


def test_preserves_the_authors_existing_rules():
    out, _ = apply_to_content(_page())
    assert "ff-el-input" in out
    assert "padding: 5px" in out


def test_no_block_is_a_no_op():
    raw = "<div>no form here</div>"
    out, result = apply_to_content(raw)
    assert out == raw
    assert not result.changed


def test_block_json_stays_parseable():
    out, _ = apply_to_content(_page())
    import re
    m = re.search(r"<!-- wp:fluentfom/guten-block (\{.*?\}) /?-->", out, re.S)
    assert m, "block comment was destroyed"
    attrs = json.loads(m.group(1).replace("\u002d\u002d", "--"))
    assert attrs["formId"] == "3"
