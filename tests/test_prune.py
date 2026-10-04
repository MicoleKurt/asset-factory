"""Guards on the only operation in the workflow that deletes content.

An earlier version of the section pass used a heuristic — "little text, lots
of markup" — and proposed deleting the testimonials, the FAQ accordions and
the service-area list. These tests encode the boundary it got wrong.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from asset_factory import prune  # noqa: E402


def block(unique_id: str, inner: str) -> str:
    return (
        '<!-- wp:generateblocks/element {"uniqueId":"' + unique_id + '"} -->'
        f"<div>{inner}</div>"
        "<!-- /wp:generateblocks/element -->"
    )


# --- what may be removed ----------------------------------------------------

@pytest.mark.parametrize("inner", [
    "<p>{{SERVICE_NAME}}</p><p>{{SERVICE_DESCRIPTION}}</p>",
    "<p>“{{CUSTOMER_TESTIMONIAL}}”</p><p>{{CUSTOMER_NAME}}</p>",
    "<p>{{BENEFIT_TITLE}}</p><p>{{BENEFIT_DESCRIPTION}}</p>",
    "<p>{{A}} / {{B}} — {{C}}</p>",
])
def test_placeholder_only_blocks_are_removable(inner):
    assert prune.is_empty_of_content(block("x", inner)) is True


# --- what must NEVER be removed --------------------------------------------

@pytest.mark.parametrize("inner", [
    "<p>Priya Shah — La Mesa, CA</p><p>Rodent removal and exclusion</p>",
    "<p>Do you serve all of East County, CA?</p>",
    "<p>Areas We Serve</p><p>El Cajon</p><p>La Mesa</p>",
    "<p>Hear What Our Clients Say</p>",
    "<p>1</p>",                                  # process step number
    "<p>{{SERVICE_NAME}}</p><p>Termite Control</p>",   # mixed
    "<p></p>",                                   # empty: nothing to judge
])
def test_content_bearing_blocks_are_never_removable(inner):
    assert prune.is_empty_of_content(block("x", inner)) is False


def test_a_block_with_no_placeholder_is_never_removable():
    """Emptiness alone is not grounds for deletion."""
    assert prune.is_empty_of_content(block("x", "<p>   </p>")) is False


# --- structural integrity ---------------------------------------------------

def test_span_covers_the_whole_block_including_children():
    inner = block("child", "<p>{{X}}</p>")
    raw = "before" + block("parent", inner) + "after"
    span = prune.find_block_span(raw, "parent")
    segment = raw[span[0]:span[1]]
    assert segment.startswith("<!-- wp:generateblocks/element")
    assert segment.endswith("<!-- /wp:generateblocks/element -->")
    assert segment.count("<!-- wp:") == segment.count("<!-- /wp:")


def test_removal_keeps_block_comments_balanced():
    raw = "<p>keep</p>" + block("a", "<p>{{X}}</p>") + "<p>keep</p>"
    assert prune.balanced(raw)
    removals = prune.find_removable(raw)
    updated, ok = prune.apply_removals(raw, removals)
    assert ok is True
    assert prune.balanced(updated)
    assert "{{X}}" not in updated
    assert updated.count("keep") == 2


def test_parent_and_child_are_not_both_removed():
    inner = block("child", "<p>{{X}}</p>")
    raw = block("parent", inner)
    removals = prune.find_removable(raw)
    assert len(removals) == 1
    assert removals[0].unique_id == "parent"


def test_unbalanced_result_is_discarded():
    raw = "<p>x</p>" + block("a", "<p>{{X}}</p>")
    bad = [prune.Removal("a", "test", 0, 12, "", [])]  # deliberately wrong span
    updated, ok = prune.apply_removals(raw, bad)
    assert ok is False
    assert updated == raw, "unbalanced markup must not be returned"


# --- the section pass is explicit, never inferred ---------------------------

def test_named_section_is_removed_when_it_matches_config():
    raw = block("sect", "<h2>Our Commercial Pest Control Services</h2>")
    found = prune.find_orphan_sections(
        raw, ["sect"], {"sect": "Our Commercial Pest Control Services"})
    assert len(found) == 1


def test_named_section_is_refused_when_text_does_not_match():
    """Config naming a block is not enough; the content must agree."""
    raw = block("sect", "<h2>Hear What Our Clients Say</h2>")
    found = prune.find_orphan_sections(
        raw, ["sect"], {"sect": "Our Commercial Pest Control Services"})
    assert found == []


def test_unnamed_sections_are_never_touched():
    """No section is removable without being named in config."""
    raw = block("sect", "<h2>Our Commercial Pest Control Services</h2>")
    assert prune.find_orphan_sections(raw, [], {}) == []


def test_named_section_holding_an_image_is_refused():
    raw = block("sect", '<h2>Short heading</h2><img src="x.webp">')
    found = prune.find_orphan_sections(raw, ["sect"], {"sect": "Short heading"})
    assert found == []


def test_named_section_with_long_text_is_refused():
    long_text = "word " * 60
    raw = block("sect", f"<h2>Short heading</h2><p>{long_text}</p>")
    found = prune.find_orphan_sections(raw, ["sect"], {"sect": "Short heading"})
    assert found == []
