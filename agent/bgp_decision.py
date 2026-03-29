from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DecisionAction:
    """
    A normalized action inside the agent decision model.

    This is intentionally simpler than the raw MCP action payload. The goal is to
    give the agent one clean internal shape for:
    safe actions
    gated actions
    suppressed duplicate actions
    """

    action_id: str
    title: str
    summary: str
    action_type: str
    risk_level: str
    approval_required: bool
    blocked: bool
    target: dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    prerequisites: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    rollback_hint: str | None = None
    suppressed_action_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class BgpDecision:
    """
    Agent level decision object built from the lattice diagnostics response.

    incident_id is derived from the grouped alert dedupe key when available.
    If there is no grouped alert, the agent still builds a stable incident id.
    """

    incident_id: str
    summary: str
    root_cause: str
    fabric: str
    device: str
    execution_enabled: bool
    approval_required: bool
    safe_actions: list[DecisionAction] = field(default_factory=list)
    gated_actions: list[DecisionAction] = field(default_factory=list)
    suppressed_actions: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)


def build_bgp_decision(response: dict[str, Any]) -> BgpDecision:
    """
    Build an approval ready decision object from the lattice diagnostics response.

    Design goals:
    keep execution disabled
    preserve grouped incident context
    reduce duplicate child actions when a parent grouped incident exists
    produce one clean decision model for the agent loop
    """
    fabric = str(response.get("fabric", "default"))
    device = str(response.get("device", "unknown"))
    diagnosis = response.get("diagnosis", {})
    if not isinstance(diagnosis, dict):
        diagnosis = {}

    alert = diagnosis.get("alert")
    if not isinstance(alert, dict):
        alert = None

    approval_summary = diagnosis.get("approval_summary", {})
    if not isinstance(approval_summary, dict):
        approval_summary = {}

    incident_id = _build_incident_id(
        fabric=fabric,
        device=device,
        diagnosis=diagnosis,
        alert=alert,
    )

    raw_actions = diagnosis.get("proposed_actions", [])
    if not isinstance(raw_actions, list):
        raw_actions = []

    safe_actions: list[DecisionAction] = []
    gated_actions: list[DecisionAction] = []
    suppressed_actions: list[str] = []

    # When a grouped alert exists, the agent should not surface duplicate child gated
    # remediations independently. Instead it should consolidate them into one approval
    # object per action type and root cause area.
    if alert is not None:
        consolidated_gated, suppressed = _consolidate_gated_actions(
            incident_id=incident_id,
            raw_actions=raw_actions,
        )
        gated_actions.extend(consolidated_gated)
        suppressed_actions.extend(suppressed)

        for item in raw_actions:
            action = _to_decision_action(item)
            if action is None:
                continue

            if action.approval_required:
                # Duplicates are handled by consolidated_gated above.
                continue

            safe_actions.append(action)
    else:
        for item in raw_actions:
            action = _to_decision_action(item)
            if action is None:
                continue

            if action.approval_required:
                gated_actions.append(action)
            else:
                safe_actions.append(action)

    safe_actions = _dedupe_actions_preserve_order(safe_actions)
    gated_actions = _dedupe_actions_preserve_order(gated_actions)

    approval_required = bool(approval_summary.get("approval_required_count", 0) > 0)

    return BgpDecision(
        incident_id=incident_id,
        summary=str(diagnosis.get("summary", "BGP decision generated")),
        root_cause=str(diagnosis.get("root_cause", "unknown")),
        fabric=fabric,
        device=device,
        execution_enabled=bool(approval_summary.get("execution_enabled", False)),
        approval_required=approval_required,
        safe_actions=safe_actions,
        gated_actions=gated_actions,
        suppressed_actions=suppressed_actions,
        evidence={
            "validation_summary": diagnosis.get("validation_summary", {}),
            "diagnosis_counts": diagnosis.get("diagnosis_counts", {}),
            "alert": alert,
        },
    )


def decision_to_dict(decision: BgpDecision) -> dict[str, Any]:
    """
    Convert the decision model into a user and log friendly dict.
    """
    return {
        "incident_id": decision.incident_id,
        "summary": decision.summary,
        "root_cause": decision.root_cause,
        "fabric": decision.fabric,
        "device": decision.device,
        "execution_enabled": decision.execution_enabled,
        "approval_required": decision.approval_required,
        "safe_actions": [_action_to_dict(action) for action in decision.safe_actions],
        "gated_actions": [_action_to_dict(action) for action in decision.gated_actions],
        "suppressed_actions": decision.suppressed_actions,
        "evidence": decision.evidence,
    }


def summarize_bgp_decision(decision: BgpDecision) -> str:
    """
    Build a compact operator friendly summary line for logs.
    """
    return (
        f"[nre_agent] incident_id={decision.incident_id} "
        f"root_cause={decision.root_cause} "
        f"safe_actions={len(decision.safe_actions)} "
        f"gated_actions={len(decision.gated_actions)} "
        f"suppressed_actions={len(decision.suppressed_actions)} "
        f"approval_required={decision.approval_required} "
        f"execution_enabled={decision.execution_enabled}"
    )


def _build_incident_id(
    *,
    fabric: str,
    device: str,
    diagnosis: dict[str, Any],
    alert: dict[str, Any] | None,
) -> str:
    """
    Prefer the grouped alert dedupe key because it is the best stable incident id.
    """
    if alert is not None and "dedupe_key" in alert:
        return str(alert["dedupe_key"])

    root_cause = str(diagnosis.get("root_cause", "unknown"))
    return f"fabric:{fabric}:device:{device}:root:{root_cause}"


def _to_decision_action(item: Any) -> DecisionAction | None:
    """
    Convert raw proposed action dict into agent DecisionAction.
    """
    if not isinstance(item, dict):
        return None

    action_id = str(item.get("action_id", "unknown_action"))

    prerequisites = item.get("prerequisites", [])
    if not isinstance(prerequisites, list):
        prerequisites = []

    commands = item.get("commands", [])
    if not isinstance(commands, list):
        commands = []

    target = item.get("target", {})
    if not isinstance(target, dict):
        target = {}

    return DecisionAction(
        action_id=action_id,
        title=str(item.get("title", action_id)),
        summary=str(item.get("summary", "")),
        action_type=str(item.get("action_type", "unknown")),
        risk_level=str(item.get("risk_level", "low")),
        approval_required=bool(item.get("approval_required", False)),
        blocked=bool(item.get("blocked", False)),
        target=target,
        rationale=str(item.get("rationale", "")),
        prerequisites=[str(x) for x in prerequisites],
        commands=[str(x) for x in commands],
        rollback_hint=(
            str(item.get("rollback_hint"))
            if item.get("rollback_hint") is not None
            else None
        ),
    )


def _consolidate_gated_actions(
    *,
    incident_id: str,
    raw_actions: list[dict[str, Any]],
) -> tuple[list[DecisionAction], list[str]]:
    """
    Consolidate duplicate child gated actions when a parent grouped incident exists.

    Example:
    two child "propose_session_reset" actions should not appear as separate approval
    objects for the same grouped incident. The agent should surface one consolidated
    approval item and keep the individual child action ids as suppressed evidence.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    suppressed_action_ids: list[str] = []

    for item in raw_actions:
        if not isinstance(item, dict):
            continue

        if not bool(item.get("approval_required", False)):
            continue

        action_type = str(item.get("action_type", "unknown"))
        risk_level = str(item.get("risk_level", "low"))
        approval_reason = str(item.get("approval_reason", ""))

        # This grouping key intentionally collapses duplicate child actions of the same
        # action class under one grouped incident.
        group_key = f"{action_type}:{risk_level}:{approval_reason}"
        grouped.setdefault(group_key, []).append(item)

    consolidated: list[DecisionAction] = []

    for group_key, items in grouped.items():
        first = items[0]
        action_type = str(first.get("action_type", "unknown"))

        targets: list[dict[str, Any]] = []
        prerequisites: list[str] = []
        commands: list[str] = []
        suppressed_ids: list[str] = []

        for item in items:
            target = item.get("target", {})
            if isinstance(target, dict):
                targets.append(target)

            raw_prereqs = item.get("prerequisites", [])
            if isinstance(raw_prereqs, list):
                for entry in raw_prereqs:
                    text = str(entry)
                    if text not in prerequisites:
                        prerequisites.append(text)

            raw_commands = item.get("commands", [])
            if isinstance(raw_commands, list):
                for entry in raw_commands:
                    text = str(entry)
                    if text not in commands:
                        commands.append(text)

            action_id = str(item.get("action_id", "unknown_action"))
            suppressed_ids.append(action_id)

        # One child item alone does not need consolidation, but we still normalize it
        # into the same decision shape for consistency.
        if len(items) == 1:
            raw = items[0]
            action = _to_decision_action(raw)
            if action is not None:
                consolidated.append(action)
            continue

        consolidated.append(
            DecisionAction(
                action_id=f"consolidated:{incident_id}:{action_type}",
                title=f"Consolidated approval for {action_type}",
                summary=(
                    f"{len(items)} child actions were collapsed into one approval object "
                    f"for grouped incident handling"
                ),
                action_type=action_type,
                risk_level=str(first.get("risk_level", "medium")),
                approval_required=True,
                blocked=bool(first.get("blocked", True)),
                target={
                    "grouped_incident_id": incident_id,
                    "affected_targets": targets,
                },
                rationale=(
                    "Duplicate child remediations were suppressed because a parent grouped "
                    "incident exists and approvals should focus on the shared dependency."
                ),
                prerequisites=prerequisites,
                commands=commands,
                rollback_hint=(
                    str(first.get("rollback_hint"))
                    if first.get("rollback_hint") is not None
                    else None
                ),
                suppressed_action_ids=suppressed_ids,
            )
        )
        suppressed_action_ids.extend(suppressed_ids)

    return consolidated, suppressed_action_ids


def _dedupe_actions_preserve_order(actions: list[DecisionAction]) -> list[DecisionAction]:
    """
    Deduplicate actions by action id while preserving order.
    """
    unique: list[DecisionAction] = []
    seen_ids: set[str] = set()

    for action in actions:
        if action.action_id in seen_ids:
            continue
        seen_ids.add(action.action_id)
        unique.append(action)

    return unique


def _action_to_dict(action: DecisionAction) -> dict[str, Any]:
    return {
        "action_id": action.action_id,
        "title": action.title,
        "summary": action.summary,
        "action_type": action.action_type,
        "risk_level": action.risk_level,
        "approval_required": action.approval_required,
        "blocked": action.blocked,
        "target": action.target,
        "rationale": action.rationale,
        "prerequisites": action.prerequisites,
        "commands": action.commands,
        "rollback_hint": action.rollback_hint,
        "suppressed_action_ids": action.suppressed_action_ids,
    }
