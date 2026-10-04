"""Regression tests for the variable sheet contract.

Each test encodes a way this automation could silently publish wrong data.
"""
from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from asset_factory.model import Reason, Severity, Status  # noqa: E402
from asset_factory.sheet import load_variable_sheet  # noqa: E402

SHEET = pathlib.Path(__file__).resolve().parents[1] / "inputs" / "site-variable-sheet.xlsx"


@pytest.fixture(scope="module")
def vs():
    return load_variable_sheet(SHEET)


# --- the phantom value ------------------------------------------------------

def test_numeric_zero_phone_is_never_applicable(vs):
    """The supplied sheet holds 0.0 in PHONE_NUMBER with status 'Missing'.

    The cell is non-empty and truthy. Any implementation keyed on presence
    rather than status publishes '0.0' as the business phone number.
    """
    phone = vs["{{PHONE_NUMBER}}"]
    assert phone.raw_value is not None, "fixture drift: cell is no longer populated"
    assert str(phone.raw_value).strip() != ""
    assert phone.status is Status.MISSING
    assert phone.applicable is False
    assert phone.primary_reason is Reason.PHANTOM_VALUE
    assert vs.approved("{{PHONE_NUMBER}}") is None


def test_phantom_value_is_reported_with_the_offending_value(vs):
    finding = vs["{{PHONE_NUMBER}}"].findings[0]
    assert "0.0" in finding.detail
    assert "must not be written" in finding.detail


# --- conflicting values -----------------------------------------------------

def test_conflicting_cta_text_is_not_resolved(vs):
    cta = vs["{{PRIMARY_CTA_TEXT}}"]
    assert cta.status is Status.CONFLICTING
    assert cta.applicable is False
    assert len(cta.candidates) == 2
    assert vs.approved("{{PRIMARY_CTA_TEXT}}") is None


def test_conflicting_cta_is_not_inferred_from_adjacent_approved_values(vs):
    """CTA_HEADING and FORM_TITLE are both the approved string 'Request a
    Quote', which makes candidate A look corroborated. Two approved values
    sitting nearby are not an approval of a third, conflicting one.
    """
    assert vs.approved("{{CTA_HEADING}}") == "Request a Quote"
    assert vs.approved("FORM_TITLE") == "Request a Quote"
    assert vs.approved("{{PRIMARY_CTA_TEXT}}") is None


# --- confirmation-required --------------------------------------------------

def test_main_city_is_not_inferred_from_seo_titles(vs):
    """Every approved SEO title contains 'East County, CA', which makes
    MAIN_CITY look derivable. It is not supplied and must stay unresolved.
    """
    assert "East County" in (vs.approved("SERVICE_SEO_TITLE") or "")
    assert vs["{{MAIN_CITY}}"].status is Status.NEEDS_CONFIRMATION
    assert vs.approved("{{MAIN_CITY}}") is None


@pytest.mark.parametrize(
    "key",
    [
        "{{PRIMARY_CTA_URL}}",
        "{{SECONDARY_CTA_TEXT}}",
        "{{SECONDARY_CTA_URL}}",
        "{{FORM_DESTINATION}}",
        "{{HIGH_TICKET_SERVICES}}",
    ],
)
def test_confirmation_required_fields_are_never_applied(vs, key):
    assert vs.approved(key) is None


@pytest.mark.parametrize("key", ["{{DOMAIN}}", "{{EMAIL}}", "{{PHONE_TEL_LINK}}"])
def test_missing_fields_are_never_applied(vs, key):
    assert vs.approved(key) is None


# --- the opposite failure: refusing approved work ---------------------------

def test_approved_values_are_applied(vs):
    assert vs.approved("{{BRAND_NAME}}") == "East County Exterminator"
    assert vs.approved("HOMEPAGE_H1") == "Pest Control Built Around the Property and the Pest"
    assert vs.approved("HEADER_LOGO_TEXT") == "East County Exterminator"


def test_overlong_seo_title_is_advisory_not_blocking(vs):
    """LOCATION_SEO_TITLE is 63 characters, past Rank Math's 60-char guidance.

    It is Approved, so it is written. Declining to apply an approved value
    would be inventing a decision the sheet did not delegate. The length is
    reported instead.
    """
    title = vs["LOCATION_SEO_TITLE"]
    assert title.applicable is True
    assert vs.approved("LOCATION_SEO_TITLE") is not None
    assert len(title.advisories) == 1
    assert title.advisories[0].severity is Severity.ADVISORY


# --- structural invariants --------------------------------------------------

def test_no_variable_is_both_applicable_and_blocked(vs):
    for var in vs.variables.values():
        blocking = [f for f in var.findings if f.severity is Severity.BLOCKING]
        assert not (var.applicable and blocking), var.key


def test_every_non_applicable_variable_has_a_reason(vs):
    for var in vs.exceptions:
        assert var.primary_reason is not None, var.key
        assert var.findings, var.key


def test_variable_set_has_no_promote_path():
    """Safety property: there is no code path that makes a field usable."""
    from asset_factory.model import Variable

    assert not any(
        name for name in dir(Variable) if "promote" in name or "approve" in name
    )


# --- derived values: transform an approved value, never invent one ----------

def test_category_label_only_removes_words():
    """A derivation may drop and recase words. It may never add one."""
    from asset_factory.contentfill import derive_category_label

    for source in ("Pest control services", "Pest Control Services",
                   "Termite Control", "Rodent removal and exclusion"):
        derived = derive_category_label(source)
        source_words = {w.lower().strip(".,") for w in source.split()}
        derived_words = {w.lower().strip(".,") for w in derived.split()}
        assert derived_words <= source_words, (
            f"derivation introduced a word not in {source!r}: {derived!r}"
        )


def test_category_label_expected_output():
    from asset_factory.contentfill import derive_category_label

    assert derive_category_label("Pest control services") == "Pest Control"
    assert derive_category_label("Termite Control") == "Termite Control"


def test_derived_source_is_an_approved_variable(vs):
    """The derivation must read from an Approved cell, not an arbitrary one."""
    from asset_factory.contentfill import DERIVED

    for _, (source_key, _) in DERIVED.items():
        assert vs.approved(source_key), (
            f"{source_key} is not Approved; nothing may be derived from it"
        )


def test_derivation_refuses_when_source_is_not_approved():
    """If the source variable were ever unapproved, nothing may be filled."""
    from asset_factory.contentfill import DERIVED, TRANSFORMS

    for _, (_, transform) in DERIVED.items():
        assert TRANSFORMS[transform]("") == ""


# --- focus keywords: derived from approved values, never invented -----------

def test_focus_keyword_words_all_come_from_the_approved_source(vs):
    """A keyword may drop words from its source. It may never add one."""
    import yaml
    from pathlib import Path
    from asset_factory.plan import derive_focus_keyword, keyword_is_traceable

    config = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "config" / "site.yml")
        .read_text(encoding="utf8"))

    checked = 0
    for spec in config["pages"]:
        source_key = spec.get("focus_keyword_from")
        if not source_key:
            continue
        approved = vs.approved(source_key)
        assert approved, f"{source_key} must be Approved to derive a keyword"
        keyword = derive_focus_keyword(approved)
        assert keyword, f"empty keyword derived for {spec['id']}"
        assert keyword_is_traceable(keyword, approved), (
            f"{spec['id']}: keyword {keyword!r} introduces a word not present "
            f"in {source_key} ({approved!r})"
        )
        checked += 1
    assert checked == 4, f"expected 4 pages with a keyword source, got {checked}"


def test_keyword_guard_rejects_an_invented_word():
    from asset_factory.plan import keyword_is_traceable

    source = "Termite Control in East County, CA"
    assert keyword_is_traceable("Termite Control", source)
    assert not keyword_is_traceable("cheap termite control", source)
    assert not keyword_is_traceable("best exterminator", source)


def test_keyword_source_must_be_an_approved_variable(vs):
    """Every configured keyword source must itself be Approved."""
    import yaml
    from pathlib import Path

    config = yaml.safe_load(
        (Path(__file__).resolve().parents[1] / "config" / "site.yml")
        .read_text(encoding="utf8"))
    for spec in config["pages"]:
        source_key = spec.get("focus_keyword_from")
        if source_key:
            assert vs.approved(source_key), (
                f"{source_key} is not Approved; nothing may be derived from it"
            )
