"""Builds a change plan. Never writes -- planning and applying are separate."""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .model import Reason
from .sheet import VariableSet

ENV_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")


def _expand(value: Any) -> Any:
    """Resolve ${ENV_VAR} references inside the config."""
    if isinstance(value, str):
        return ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def load_config(path: str | Path) -> dict:
    with open(path, encoding="utf8") as fh:
        return _expand(yaml.safe_load(fh))


@dataclass
class Change:
    target: str          # "Homepage" / "Site title"
    target_kind: str     # page | option | menu_item
    target_id: Any
    field: str
    variable: str
    current: str
    proposed: str
    advisories: list[str] = field(default_factory=list)

    @property
    def is_noop(self) -> bool:
        return (self.current or "").strip() == (self.proposed or "").strip()

    @property
    def fingerprint(self) -> str:
        raw = f"{self.target_kind}:{self.target_id}:{self.field}:{self.proposed}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]


@dataclass
class Skip:
    target: str
    field: str
    variable: str
    reason: str
    detail: str
    action_required: str


@dataclass
class Plan:
    changes: list[Change] = field(default_factory=list)
    noops: list[Change] = field(default_factory=list)
    skips: list[Skip] = field(default_factory=list)

    def add(self, change: Change) -> None:
        (self.noops if change.is_noop else self.changes).append(change)


ACTION_REQUIRED = {
    Reason.NOT_SUPPLIED:
        "Supply the approved value in the Site Variable Sheet, then re-run.",
    Reason.PHANTOM_VALUE:
        "Correct the cell and set its status to Approved, then re-run. The "
        "value currently in the cell is not usable.",
    Reason.FAILED_FORMAT:
        "Correct the value so it matches the expected format, then re-run.",
    Reason.MULTIPLE_CANDIDATES:
        "Select one of the supplied candidates and mark it Approved. The "
        "workflow will not choose between them.",
    Reason.AWAITING_CONFIRMATION:
        "Obtain human confirmation and mark the value Approved, then re-run.",
    Reason.UNKNOWN_STATUS:
        "Set a recognised status (Approved / Missing / Conflicting / Human "
        "confirmation required).",
    Reason.DEPENDENCY_UNRESOLVED:
        "Resolve the field this one depends on first.",
}


def build_plan(config: dict, variables: VariableSet, state: dict) -> Plan:
    """state: {"site_title": str, "pages": {config_id: {field: current_value}}}"""
    plan = Plan()

    observed = state.get("globals", {})
    for spec in config.get("globals", []):
        _plan_field(
            plan, variables,
            target=spec["label"], target_kind=spec["kind"],
            target_id=spec.get("option"), field_name=spec.get("option", spec["id"]),
            variable_key=spec["variable"],
            current=observed.get(spec["id"], ""),
        )

    for spec in config.get("pages", []):
        page_state = state.get("pages", {}).get(spec["id"], {})
        label = f'{spec["page_type"]} page'
        for field_name, variable_key in spec["fields"].items():
            _plan_field(
                plan, variables,
                target=label, target_kind="page",
                target_id=page_state.get("id"), field_name=field_name,
                variable_key=variable_key,
                current=page_state.get(field_name, ""),
            )

    # Menu items are matched by their live title. After a successful run the
    # original label no longer exists, so a second run must recognise the
    # already-applied title and treat it as a no-op rather than a mismatch.
    live_items = state.get("menu_items", {})
    for menu in config.get("menus", []):
        for item in menu.get("items", []):
            match_title = item["match_title"]
            var = variables.get(item["variable"])
            proposed = var.value if var and var.applicable else None

            if match_title in live_items:
                current, item_id = match_title, live_items[match_title]
            elif proposed and proposed in live_items:
                # Already renamed by an earlier run.
                current, item_id = proposed, live_items[proposed]
            else:
                current, item_id = match_title, None

            _plan_field(
                plan, variables,
                target=f'{menu["label"]} nav',
                target_kind="menu_item", target_id=item_id,
                field_name="title", variable_key=item["variable"],
                current=current,
            )

    return plan


def _plan_field(plan: Plan, variables: VariableSet, *, target: str,
                target_kind: str, target_id: Any, field_name: str,
                variable_key: str, current: str) -> None:
    var = variables.get(variable_key)

    if var is None:
        plan.skips.append(Skip(
            target=target, field=field_name, variable=variable_key,
            reason=Reason.NOT_SUPPLIED.value,
            detail=f"{variable_key} is not present in the variable sheet",
            action_required="Add the variable to the sheet, or correct the "
                            "config key that references it.",
        ))
        return

    if not var.applicable:
        reason = var.primary_reason or Reason.NOT_SUPPLIED
        blocking = [f for f in var.findings if f.severity.value == "blocking"]
        plan.skips.append(Skip(
            target=target, field=field_name, variable=variable_key,
            reason=reason.value,
            detail=blocking[0].detail if blocking else "not applicable",
            action_required=ACTION_REQUIRED.get(reason, "Human review required."),
        ))
        return

    plan.add(Change(
        target=target, target_kind=target_kind, target_id=target_id,
        field=field_name, variable=variable_key,
        current=current or "", proposed=var.value or "",
        advisories=[f.detail for f in var.advisories],
    ))


def derive_focus_keyword(approved_value: str) -> str:
    """Focus keyword from an Approved value. Words are only ever removed.

    'Pest control services' -> 'Pest control'
    'Termite Control in East County, CA' -> unchanged

    Case is preserved rather than normalised, so the result stays traceable to
    the cell it came from.
    """
    from .contentfill import _TRAILING_NOUNS

    words = (approved_value or "").strip().split()
    while words and words[-1].lower().strip(",.") in _TRAILING_NOUNS:
        words.pop()
    return " ".join(words)


def keyword_is_traceable(keyword: str, source_value: str) -> bool:
    """Guard: every word of the keyword must exist in the approved source.

    This is the invariant that makes a derived keyword defensible rather than
    invented. If it ever fails, the keyword is not written.
    """
    import re as _re

    def words(text: str) -> set[str]:
        return {w for w in _re.split(r"[^a-z0-9]+", (text or "").lower()) if w}

    return words(keyword) <= words(source_value)
