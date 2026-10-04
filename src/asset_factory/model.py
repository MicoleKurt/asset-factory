"""Core domain model for the Site Variable Sheet.

Design rule that governs this whole module:

    The Status column is authoritative. Validation may only ever DEMOTE a
    field's usability, never promote it.

A value that looks usable but is not marked Approved is a *phantom value* --
it is present in the cell but carries no approval, and must never reach the
website. The supplied sheet contains at least one of these, and it is the
single most dangerous input in the dataset.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class Status(str, Enum):
    """Authoritative status as supplied by the Site Variable Sheet."""

    APPROVED = "Approved"
    MISSING = "Missing"
    CONFLICTING = "Conflicting"
    NEEDS_CONFIRMATION = "Human confirmation required"
    UNKNOWN = "Unknown"

    @property
    def is_usable(self) -> bool:
        return self is Status.APPROVED


_STATUS_ALIASES = {
    "approved": Status.APPROVED,
    "missing": Status.MISSING,
    "conflicting": Status.CONFLICTING,
    "conflict": Status.CONFLICTING,
    "human confirmation required": Status.NEEDS_CONFIRMATION,
    "confirmation required": Status.NEEDS_CONFIRMATION,
    "needs confirmation": Status.NEEDS_CONFIRMATION,
}


def normalize_status(raw: Any) -> Status:
    if raw is None:
        return Status.UNKNOWN
    return _STATUS_ALIASES.get(str(raw).strip().lower(), Status.UNKNOWN)


class Severity(str, Enum):
    """How a validation failure is handled.

    BLOCKING  - the value is semantically the wrong kind of thing. Writing it
                would publish incorrect information, so the field is demoted
                and reported instead.
    ADVISORY  - the value is the right kind of thing but has a quality concern.
                The sheet is the source of truth and the value is Approved, so
                it IS written; the concern is reported alongside it. Declining
                to apply an approved value is itself a failure.
    """

    BLOCKING = "blocking"
    ADVISORY = "advisory"


class Reason(str, Enum):
    """Why a field cannot be applied. Drives exception-report grouping."""

    NOT_SUPPLIED = "value_not_supplied"
    PHANTOM_VALUE = "value_present_but_not_approved"
    FAILED_FORMAT = "value_failed_format_validation"
    MULTIPLE_CANDIDATES = "multiple_candidate_values_supplied"
    AWAITING_CONFIRMATION = "awaiting_human_confirmation"
    UNKNOWN_STATUS = "status_not_recognised"
    DEPENDENCY_UNRESOLVED = "depends_on_an_unresolved_field"
    QUALITY_ADVISORY = "approved_value_has_a_quality_concern"


@dataclass
class Finding:
    reason: Reason
    detail: str
    severity: Severity = Severity.BLOCKING


@dataclass
class Variable:
    """One row of the site-specific variable sheet."""

    key: str
    category: str
    raw_value: Any
    status: Status
    row: int
    candidates: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    _demoted: bool = False

    # ---- derived -------------------------------------------------------
    @property
    def value(self) -> str | None:
        """The cleaned string value, or None when there is nothing usable."""
        if self.raw_value is None:
            return None
        text = str(self.raw_value).strip()
        return text or None

    @property
    def has_text(self) -> bool:
        return self.value is not None

    @property
    def applicable(self) -> bool:
        """True only when this value may be written to the website."""
        return self.status.is_usable and not self._demoted and self.has_text

    def demote(self, reason: Reason, detail: str) -> None:
        """Mark the field unusable. There is deliberately no promote()."""
        self._demoted = True
        self.findings.append(Finding(reason, detail, Severity.BLOCKING))

    def note(self, reason: Reason, detail: str) -> None:
        self.findings.append(Finding(reason, detail, Severity.BLOCKING))

    def advise(self, detail: str) -> None:
        """Record a quality concern without blocking the write."""
        self.findings.append(
            Finding(Reason.QUALITY_ADVISORY, detail, Severity.ADVISORY)
        )

    @property
    def advisories(self) -> list[Finding]:
        return [f for f in self.findings if f.severity is Severity.ADVISORY]

    @property
    def primary_reason(self) -> Reason | None:
        if self.applicable:
            return None
        blocking = [f for f in self.findings if f.severity is Severity.BLOCKING]
        if blocking:
            return blocking[0].reason
        return {
            Status.MISSING: Reason.NOT_SUPPLIED,
            Status.CONFLICTING: Reason.MULTIPLE_CANDIDATES,
            Status.NEEDS_CONFIRMATION: Reason.AWAITING_CONFIRMATION,
            Status.UNKNOWN: Reason.UNKNOWN_STATUS,
        }.get(self.status, Reason.NOT_SUPPLIED)


# ---------------------------------------------------------------------------
# Format validators
# ---------------------------------------------------------------------------

_US_PHONE = re.compile(r"^\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}$")
_TEL_LINK = re.compile(r"^tel:\+1\d{10}$")
_EMAIL = re.compile(r"^[^@\s]+@[^@\s.]+\.[^@\s]+$")
_URL = re.compile(r"^https?://[^\s]+$")
_DOMAIN = re.compile(r"^(?!https?://)[a-z0-9.-]+\.[a-z]{2,}$", re.I)
_NUMERIC = re.compile(r"^-?\d+(\.\d+)?$")


def validate_phone(text: str) -> str | None:
    # Excel/Sheets coerces an empty numeric cell to 0, which arrives here as
    # "0.0". It is truthy, non-empty, and completely wrong. Reject anything
    # that is purely numeric before pattern-matching, so the failure message
    # names the real cause instead of a vague "bad format".
    if _NUMERIC.match(text):
        return (
            f"cell contains the numeric value {text!r}, not a phone number "
            "(spreadsheet coerced an empty or numeric cell)"
        )
    if not _US_PHONE.match(text):
        return f"{text!r} is not a valid US phone number"
    return None


def validate_tel_link(text: str) -> str | None:
    if not _TEL_LINK.match(text):
        return f"{text!r} is not a valid tel: link in tel:+1XXXXXXXXXX form"
    return None


def validate_email(text: str) -> str | None:
    if not _EMAIL.match(text):
        return f"{text!r} is not a valid email address"
    return None


def validate_url(text: str) -> str | None:
    if not _URL.match(text):
        return f"{text!r} is not an absolute http(s) URL"
    return None


def validate_domain(text: str) -> str | None:
    if not _DOMAIN.match(text):
        return f"{text!r} is not a bare domain name"
    return None


def validate_slug(text: str) -> str | None:
    if text == "/":
        return None
    if not re.match(r"^/?[a-z0-9]+(?:-[a-z0-9]+)*/?$", text):
        return f"{text!r} is not a valid lowercase hyphenated slug"
    return None


def validate_meta_description(text: str) -> str | None:
    if not 50 <= len(text) <= 160:
        return f"meta description is {len(text)} characters (expected 50-160)"
    return None


def validate_seo_title(text: str) -> str | None:
    if not 15 <= len(text) <= 60:
        return f"SEO title is {len(text)} characters (expected 15-60)"
    return None


# Blocking: these check that the value is the right KIND of thing. A failure
# means writing it would put incorrect information on the website.
BLOCKING_VALIDATORS_BY_KEY = {
    "{{PHONE_NUMBER}}": validate_phone,
    "{{PHONE_TEL_LINK}}": validate_tel_link,
    "{{EMAIL}}": validate_email,
    "{{DOMAIN}}": validate_domain,
    "{{PRIMARY_CTA_URL}}": validate_url,
    "{{SECONDARY_CTA_URL}}": validate_url,
    "HOMEPAGE_SLUG": validate_slug,
}

BLOCKING_VALIDATORS_BY_SUFFIX = {
    "_SLUG": validate_slug,
}

# Advisory: the value is the right kind of thing, but search engines or the
# CMS may treat it suboptimally. Approved values still get written.
ADVISORY_VALIDATORS_BY_SUFFIX = {
    "_SEO_TITLE": validate_seo_title,
    "_META_DESCRIPTION": validate_meta_description,
}


def blocking_validator_for(key: str):
    if key in BLOCKING_VALIDATORS_BY_KEY:
        return BLOCKING_VALIDATORS_BY_KEY[key]
    for suffix, fn in BLOCKING_VALIDATORS_BY_SUFFIX.items():
        if key.endswith(suffix):
            return fn
    return None


def advisory_validator_for(key: str):
    for suffix, fn in ADVISORY_VALIDATORS_BY_SUFFIX.items():
        if key.endswith(suffix):
            return fn
    return None
