"""Guards for the approved first-brand-mention link instruction."""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from asset_factory.linking import link_first_brand_mention, verify  # noqa: E402

BRAND = "East County Exterminator"


def test_links_first_paragraph_mention():
    raw = '<p class="gb-text">East County Exterminator provides pest control.</p>'
    out, result = link_first_brand_mention(raw, BRAND)
    assert result.changed
    assert '<a href="/">East County Exterminator</a>' in out
    assert verify(out, BRAND)


def test_is_idempotent():
    raw = '<p>East County Exterminator provides pest control.</p>'
    once, _ = link_first_brand_mention(raw, BRAND)
    twice, result = link_first_brand_mention(once, BRAND)
    assert twice == once
    assert not result.changed
    assert once.count("<a href=") == 1


def test_never_edits_inside_a_block_comment():
    """Block delimiters carry JSON. Rewriting one corrupts the block."""
    raw = ('<!-- wp:generateblocks/text {"uniqueId":"x",'
           '"metadata":{"name":"East County Exterminator card"}} -->\n'
           '<p>East County Exterminator provides pest control.</p>')
    out, result = link_first_brand_mention(raw, BRAND)
    assert result.changed
    comment = out[:out.index("-->") + 3]
    assert "<a href=" not in comment, "link was inserted into block JSON"
    assert out.count("<a href=") == 1


def test_skips_headings():
    raw = ('<h1>East County Exterminator</h1>'
           '<p>East County Exterminator provides pest control.</p>')
    out, result = link_first_brand_mention(raw, BRAND)
    assert result.changed
    assert "<h1>East County Exterminator</h1>" in out, "heading was modified"
    assert out.count("<a href=") == 1


def test_never_links_inside_an_attribute():
    raw = '<img alt="East County Exterminator van" src="x.webp">'
    out, result = link_first_brand_mention(raw, BRAND)
    assert not result.changed
    assert out == raw


def test_no_nested_anchors():
    raw = '<p><a href="/x">East County Exterminator</a> provides control.</p>'
    out, result = link_first_brand_mention(raw, BRAND)
    assert not result.changed
    assert out.count("<a ") == 1


def test_uses_root_relative_href_not_a_hostname():
    """{{DOMAIN}} is Missing, so no absolute URL is approved for this site."""
    raw = "<p>East County Exterminator provides pest control.</p>"
    out, _ = link_first_brand_mention(raw, BRAND)
    assert 'href="/"' in out
    assert "http" not in out


def test_reports_when_no_mention_exists():
    out, result = link_first_brand_mention("<p>Nothing here.</p>", BRAND)
    assert not result.changed
    assert "no eligible mention" in result.reason
