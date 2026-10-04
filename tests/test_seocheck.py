"""Guards on keyword selection.

The selector optimises a score, which makes it the most likely part of this
workflow to start gaming the checker instead of serving the site. Each test
below encodes a way it did exactly that during development.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from asset_factory.plan import keyword_is_traceable  # noqa: E402
from asset_factory.seocheck import (  # noqa: E402
    candidate_keywords,
    evaluate,
    is_well_formed,
    select_keyword,
    _norm,
)

SOURCE = "Pest Control in Lakeside, CA"

HTML = """<html><head><title>Pest Control in Lakeside, CA | Termite</title>
<meta name="description" content="Pest control services in Lakeside."></head>
<body><main><h1>Pest Control in Lakeside, CA</h1>
<h2>Termite Control</h2><p>East County Exterminator provides pest control in
Lakeside, CA for homes and businesses. Pest control in Lakeside covers termite,
rodent and insect work.</p><img src="a.webp" alt="A home"><a href="/">home</a>
</main></body></html>"""


def test_candidates_only_ever_remove_words():
    for candidate in candidate_keywords(SOURCE):
        assert keyword_is_traceable(candidate, SOURCE), candidate


def test_rejects_grammatical_fragments():
    """'Pest Control in' scored as well as the full phrase and won on a
    shorter-is-better tiebreak. It is not a phrase anyone searches."""
    assert not is_well_formed("Pest Control in")
    assert not is_well_formed("Termite Control in")
    assert not is_well_formed("Services for")
    assert is_well_formed("Pest Control in Lakeside")
    assert is_well_formed("Termite Control")


def test_rejects_single_word_keywords():
    """A one-word keyword scores highest purely because a short string matches
    in more places. Optimising for the checker is the failure being guarded."""
    assert not is_well_formed("Pest")
    assert not is_well_formed("Termite")


def test_selection_never_reuses_a_keyword():
    taken = {_norm("Pest Control in Lakeside, CA")}
    keyword, _, _ = select_keyword(
        HTML, "https://example.test/lakeside/", SOURCE, taken,
        keyword_is_traceable)
    assert _norm(keyword) not in taken


def test_selected_keyword_is_traceable_and_well_formed():
    keyword, result, ranked = select_keyword(
        HTML, "https://example.test/lakeside/", SOURCE, set(),
        keyword_is_traceable)
    assert keyword_is_traceable(keyword, SOURCE)
    assert is_well_formed(keyword)
    assert result.total > 0
    assert ranked


def test_ties_prefer_the_longer_more_specific_phrase():
    ranked = [(56, "East County", None), (56, "East County Exterminator", None)]
    ranked.sort(key=lambda row: (-row[0], -len(row[1])))
    assert ranked[0][1] == "East County Exterminator"


def test_blocked_checks_are_not_reported_as_fixable():
    """A failure caused by approved content must never look actionable."""
    result = evaluate(HTML, "Pest Control in Lakeside", "https://example.test/x/")
    for check in result.failures:
        if check.blocked_by:
            assert not check.fixable, check.name


def test_evaluate_is_deterministic():
    url = "https://example.test/lakeside/"
    first = evaluate(HTML, "Pest Control in Lakeside", url)
    second = evaluate(HTML, "Pest Control in Lakeside", url)
    assert first.score == second.score
    assert [c.passed for c in first.checks] == [c.passed for c in second.checks]
