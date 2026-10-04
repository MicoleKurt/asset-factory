"""Regression tests for image placement in GenerateBlocks markup."""
from __future__ import annotations

import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from asset_factory import images  # noqa: E402

URL = "https://example.test/wp-content/uploads/2026/08/photo.webp"
ALT = "Foundation perimeter where soil meets the concrete stem wall"

# A block that carries an empty alt key, as the homepage hero did.
WITH_ALT = (
    '<!-- wp:generateblocks/media {"uniqueId":"aaa11111","tagName":"img",'
    '"globalClasses":["ap-hero-image"],"htmlAttributes":'
    '{"src":"https://patterns.generatepress.com/x/placeholder1280x720.png",'
    '"title":"Sample","alt":""}} -->\n'
    '<img class="ap-hero-image" '
    'src="https://patterns.generatepress.com/x/placeholder1280x720.png" '
    'title="Sample" alt="">'
)

# A block with NO alt key at all, as every non-homepage hero actually was.
# String substitution on alt="" silently does nothing here, which is how an
# image shipped with no alt text while the run reported success.
WITHOUT_ALT = (
    '<!-- wp:generateblocks/media {"uniqueId":"bbb22222","tagName":"img",'
    '"globalClasses":["gbp\\u002d\\u002dborder-radius"],"htmlAttributes":'
    '{"src":"https://patterns.generatepress.com/x/placeholder1280x720.png",'
    '"title":"magai-020325120259"}} -->\n'
    '<img class="gbp--border-radius" '
    'src="https://patterns.generatepress.com/x/placeholder1280x720.png" '
    'title="magai-020325120259">'
)


def _attrs(raw: str) -> dict:
    match = re.search(r'<!-- wp:generateblocks/media (\{.*?\}) -->', raw, re.S)
    return json.loads(match.group(1))


def test_sets_alt_when_the_key_already_exists():
    out, changed = images.ensure_slot(WITH_ALT, "aaa11111", URL, ALT)
    assert changed
    assert _attrs(out)["htmlAttributes"]["alt"] == ALT
    assert f'alt="{ALT}"' in out


def test_sets_alt_when_the_key_is_absent():
    """The bug: no alt key means nothing to substitute."""
    assert '"alt"' not in WITHOUT_ALT.split("-->")[0]
    out, changed = images.ensure_slot(WITHOUT_ALT, "bbb22222", URL, ALT)
    assert changed
    assert _attrs(out)["htmlAttributes"]["alt"] == ALT
    assert f'alt="{ALT}"' in out


def test_updates_both_the_json_and_the_img_tag():
    """Editor reads the JSON, the front end reads the tag. Both or neither."""
    out, _ = images.ensure_slot(WITHOUT_ALT, "bbb22222", URL, ALT)
    assert _attrs(out)["htmlAttributes"]["src"] == URL
    assert f'src="{URL}"' in out
    assert "patterns.generatepress.com" not in out


def test_replaces_leftover_template_titles():
    out, _ = images.ensure_slot(WITHOUT_ALT, "bbb22222", URL, ALT)
    assert "magai-020325120259" not in out
    out2, _ = images.ensure_slot(WITH_ALT, "aaa11111", URL, ALT)
    assert '"title":"Sample"' not in out2


def test_is_idempotent():
    once, changed_first = images.ensure_slot(WITHOUT_ALT, "bbb22222", URL, ALT)
    twice, changed_second = images.ensure_slot(once, "bbb22222", URL, ALT)
    assert changed_first is True
    assert changed_second is False
    assert once == twice


def test_double_hyphen_stays_escaped_inside_the_block_comment():
    """'--' inside an HTML comment breaks the block parser; WP escapes it."""
    out, _ = images.ensure_slot(WITHOUT_ALT, "bbb22222", URL, ALT)
    comment = out.split("-->")[0]
    assert "gbp\\u002d\\u002dborder-radius" in comment
    assert "gbp--border-radius" not in comment


def test_unknown_unique_id_is_a_no_op():
    out, changed = images.ensure_slot(WITH_ALT, "does-not-exist", URL, ALT)
    assert changed is False
    assert out == WITH_ALT


def test_filename_and_alt_validation():
    assert images.validate_filename("home-hero-residential-property.webp") is None
    assert images.validate_filename("Home Hero.PNG") is not None
    assert images.validate_alt(ALT) is None
    assert images.validate_alt("") is not None
    assert images.validate_alt("Image of a house") is not None
