from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from agent.approvals import (
    clear_approval_record,
    create_pending_approval,
    get_approval_record,
    summarize_approval_state,
    update_approval_status,
)
from agent.bgp_decision import build_bgp_decision, decision_to_dict, summarize_bgp_decision
from agent.client import call_lattice, call_lattice_bgp_diagnostics
from agent.scenarios import get_next_scenario


# In memory cooldown tracking for the current pod lifetime.
# This prevents immediate re creation of pending approval records
# right after an approved execution attempt.
_APPROVAL_COOLDOWN_UNTIL: dict[str, datetime] = {}


def _utc_now() -> str:
    """
    Return UTC timestamp for operator friendly logs.
    """
    return datetime.now(timezone.utc).isoformat()


def _utc_now_dt() -> datetime:
    """
    Return UTC datetime object.
    """
    return datetime.now(timezone.utc)


def _agent_mode() -> str:
    """
    Return the operating mode for the agent loop.

    Supported values:
    scenario
    bgp_diagnostics
    """
    return os.environ.get("NRE_AGENT_MODE", "scenario").strip().lower() or "scenario"


def _cooldown_seconds() -> int:
    """
    Cooldown window after an approved reopen and execution.

    This is intentionally simple.
    Later this can move into persistent state or policy config.
    """
    return int(os.environ.get("NRE_AGENT_APPROVAL_COOLDOWN_SECONDS", "300"))


def _set_cooldown(key: str) -> None:
    """
    Start cooldown for a key after approval is consumed.

    The key can be a scenario name in scenario mode or an incident id in BGP diagnostics
    mode. Using one common mechanism keeps the loop simple.
    """
    _APPROVAL_COOLDOWN_UNTIL[key] = _utc_now_dt() + timedelta(seconds=_cooldown_seconds())


def _get_cooldown_remaining_seconds(key: str) -> int:
    """
    Return remaining cooldown seconds for a key.
    """
    expires = _APPROVAL_COOLDOWN_UNTIL.get(key)
    if expires is None:
        return 0

    remaining = int((expires - _utc_now_dt()).total_seconds())
    return max(remaining, 0)


def _is_in_cooldown(key: str) -> bool:
    """
    Check whether the key is still in cooldown.
    """
    return _get_cooldown_remaining_seconds(key) > 0


def _extract_risk(response: dict[str, Any]) -> dict[str, Any] | None:
    """
    Pull risk block out of lattice response.
    """
    result = response.get("result")
    if not isinstance(result, dict):
        return None

    risk = result.get("risk")
    if not isinstance(risk, dict):
        return None

    return risk


def _summarize_response(scenario: str, response: dict[str, Any]) -> str:
    """
    Build a concise summary line for each scenario loop iteration.
    """
    status = str(response.get("status", "unknown"))
    message = str(response.get("message", ""))

    risk = _extract_risk(response)
    if risk is None:
        return (
            f"[nre_agent] ts={_utc_now()} "
            f"scenario={scenario} "
            f"status={status} "
            f'message=\"{message}\"'
        )

    risk_level = str(risk.get("risk_level", "unknown"))
    blast_radius = risk.get("blast_radius_score")
    requires_approval = risk.get("requires_approval")

    return (
        f"[nre_agent] ts={_utc_now()} "
        f"scenario={scenario} "
        f"status={status} "
        f"risk_level={risk_level} "
        f"blast_radius={blast_radius} "
        f"requires_approval={requires_approval} "
        f'message=\"{message}\"'
    )


def _apply_simulated_approval_override(key: str) -> None:
    """
    Apply an optional approval override before gate enforcement.

    This matters because the approval gate may otherwise hold
    the scenario or incident before the loop reaches policy handling logic.
    """
    simulated_status = os.environ.get("NRE_AGENT_APPROVAL_STATUS", "").strip().lower()
    if simulated_status not in {"approved", "rejected"}:
        return

    record = get_approval_record(key)
    if record is None:
        return

    if record.status == simulated_status:
        return

    updated = update_approval_status(key, simulated_status)
    print(
        f"[nre_agent] ts={_utc_now()} approval_key={key} "
        f"approval_transition={updated.status}",
        flush=True,
    )


def _precheck_approval_gate(key: str) -> bool:
    """
    Enforce approval state before calling lattice in scenario mode.

    Returns True when the agent is allowed to proceed.
    Returns False when the scenario should be skipped for now.

    Rules:
    pending   -> hold
    rejected  -> suppress
    approved  -> reopen and proceed once
    cooldown  -> skip repeated approval churn
    no record -> proceed
    """
    if _is_in_cooldown(key):
        remaining = _get_cooldown_remaining_seconds(key)
        print(
            f"[nre_agent] ts={_utc_now()} approval_key={key} "
            f"approval_gate=cooldown remaining_seconds={remaining}",
            flush=True,
        )
        return False

    record = get_approval_record(key)
    if record is None:
        return True

    if record.status == "pending":
        print(
            f"[nre_agent] ts={_utc_now()} approval_key={key} "
            f"approval_gate=hold approval_status=pending",
            flush=True,
        )
        return False

    if record.status == "rejected":
        print(
            f"[nre_agent] ts={_utc_now()} approval_key={key} "
            f"approval_gate=suppress approval_status=rejected",
            flush=True,
        )
        return False

    if record.status == "approved":
        print(
            f"[nre_agent] ts={_utc_now()} approval_key={key} "
            f"approval_gate=reopen approval_status=approved",
            flush=True,
        )

        # Clear the approval record so the scenario can proceed.
        # The next high risk result will not immediately recreate a
        # pending record because cooldown is applied after execution.
        clear_approval_record(key)

        print(
            f"[nre_agent] ts={_utc_now()} approval_key={key} approval_gate=proceed",
            flush=True,
        )
        return True

    return True


def _handle_policy_outcome(scenario: str, response: dict[str, Any]) -> None:
    """
    Policy aware agent behavior for traditional scenario mode.

    low
    continue

    medium
    caution

    high or approval required
    create or reuse approval record, unless cooldown is active
    """
    risk = _extract_risk(response)
    if risk is None:
        print(
            f"[nre_agent] ts={_utc_now()} scenario={scenario} policy_state=no_risk_data",
            flush=True,
        )
        return

    risk_level = str(risk.get("risk_level", "unknown"))
    requires_approval = bool(risk.get("requires_approval", False))
    blast_radius_score = int(risk.get("blast_radius_score", 0))
    reasons = [str(x) for x in risk.get("reasons", [])]

    if requires_approval or risk_level == "high":
        if _is_in_cooldown(scenario):
            remaining = _get_cooldown_remaining_seconds(scenario)
            print(
                f"[nre_agent] ts={_utc_now()} scenario={scenario} "
                f"policy_action=cooldown remaining_seconds={remaining}",
                flush=True,
            )
            return

        record = create_pending_approval(
            scenario=scenario,
            risk_level=risk_level,
            blast_radius_score=blast_radius_score,
            reasons=reasons,
        )

        print(
            f"[nre_agent] ts={_utc_now()} scenario={scenario} "
            f"policy_action=escalate approval_status={record.status} reasons={reasons}",
            flush=True,
        )

        summary = summarize_approval_state(scenario)
        if summary is not None:
            print(
                f"[nre_agent] ts={_utc_now()} scenario={scenario} approval_record={summary}",
                flush=True,
            )
        return

    if risk_level == "medium":
        print(
            f"[nre_agent] ts={_utc_now()} scenario={scenario} "
            f"policy_action=caution reasons={reasons}",
            flush=True,
        )
        return

    print(
        f"[nre_agent] ts={_utc_now()} scenario={scenario} policy_action=continue",
        flush=True,
    )


def _post_execution_bookkeeping(scenario: str, response: dict[str, Any]) -> None:
    """
    Apply post execution bookkeeping for scenario mode.

    If a scenario was reopened through approval and still came back
    high risk, enter cooldown so the agent does not immediately create
    a new pending request on the next cycle.
    """
    risk = _extract_risk(response)
    if risk is None:
        return

    risk_level = str(risk.get("risk_level", "unknown"))
    requires_approval = bool(risk.get("requires_approval", False))

    # If the scenario has no approval record now, it means the gate
    # was reopened and the record was cleared before execution.
    # If the result still requires approval, start cooldown.
    record = get_approval_record(scenario)
    if record is None and (requires_approval or risk_level == "high"):
        _set_cooldown(scenario)
        remaining = _get_cooldown_remaining_seconds(scenario)
        print(
            f"[nre_agent] ts={_utc_now()} scenario={scenario} "
            f"post_execution=cooldown_started remaining_seconds={remaining}",
            flush=True,
        )


def _load_bgp_snapshot() -> dict[str, Any]:
    """
    Load the BGP snapshot JSON from a file.

    This keeps the first nre_agent integration simple and safe:
    the agent consumes validated diagnostics input without taking on the
    responsibility of gathering raw network state itself.
    """
    path = Path(
        os.environ.get("NRE_AGENT_BGP_SNAPSHOT_FILE", "/data/bgp_snapshot.json")
    )

    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise ValueError("BGP snapshot file must contain a JSON object")

    return data


def _run_bgp_diagnostics_iteration() -> None:
    """
    Execute one BGP diagnostics and decision iteration.

    This path is intentionally non executable.
    It does all of the following:
    calls lattice diagnostics
    builds an internal decision object
    suppresses duplicate child gated actions under a grouped incident
    creates a pending approval record for the incident when needed
    """
    fabric = os.environ.get("NRE_AGENT_BGP_FABRIC", "default").strip() or "default"
    device = os.environ.get("NRE_AGENT_BGP_DEVICE", "unknown").strip() or "unknown"
    base_url = os.environ.get("NRE_AGENT_LATTICE_URL", "http://lattice:8080").strip()

    snapshot = _load_bgp_snapshot()

    response = call_lattice_bgp_diagnostics(
        fabric=fabric,
        device=device,
        snapshot=snapshot,
        base_url=base_url,
    )

    print("[nre_agent] lattice BGP diagnostics response:", flush=True)
    print(response, flush=True)

    decision = build_bgp_decision(response)

    print(summarize_bgp_decision(decision), flush=True)
    print(decision_to_dict(decision), flush=True)

    incident_key = decision.incident_id

    # Apply any simulated approval state to the incident level record. This lets you test
    # approval state transitions while execution remains disabled.
    _apply_simulated_approval_override(incident_key)

    if decision.approval_required:
        reasons = [action.summary for action in decision.gated_actions]

        record = create_pending_approval(
            scenario=incident_key,
            risk_level=_highest_gated_risk(decision),
            blast_radius_score=len(decision.gated_actions),
            reasons=reasons,
        )

        print(
            f"[nre_agent] ts={_utc_now()} incident_id={incident_key} "
            f"decision_action=approval_pending approval_status={record.status}",
            flush=True,
        )

        summary = summarize_approval_state(incident_key)
        if summary is not None:
            print(
                f"[nre_agent] ts={_utc_now()} incident_id={incident_key} "
                f"approval_record={summary}",
                flush=True,
            )
    else:
        print(
            f"[nre_agent] ts={_utc_now()} incident_id={incident_key} "
            f"decision_action=no_approval_required",
            flush=True,
        )


def _highest_gated_risk(decision: Any) -> str:
    """
    Return the highest risk seen in the gated action set.

    This is used to write a compact approval record until we extend the approval store
    to hold richer incident structures.
    """
    rank = {
        "low": 0,
        "medium": 1,
        "high": 2,
        "critical": 3,
    }

    best = "low"
    best_rank = 0

    for action in decision.gated_actions:
        risk_level = str(action.risk_level)
        risk_rank = rank.get(risk_level, 0)
        if risk_rank > best_rank:
            best = risk_level
            best_rank = risk_rank

    return best


def run_agent_loop() -> None:
    """
    Continuous outer agent loop.

    Scenario mode responsibilities:
    choose scenario
    apply approval override before gate check
    enforce approval gate
    call lattice
    summarize result
    react to policy outcome
    apply post execution bookkeeping

    BGP diagnostics mode responsibilities:
    load snapshot
    call lattice diagnostics
    build approval ready decision object
    create incident level approval record when needed
    never execute changes
    """
    interval_seconds = int(os.environ.get("NRE_AGENT_INTERVAL_SECONDS", "30"))

    print(
        f"[nre_agent] starting loop with interval={interval_seconds}s mode={_agent_mode()}",
        flush=True,
    )

    while True:
        try:
            if _agent_mode() == "bgp_diagnostics":
                _run_bgp_diagnostics_iteration()
                time.sleep(interval_seconds)
                continue

            scenario = get_next_scenario()

            print(f"[nre_agent] selected scenario: {scenario}", flush=True)

            # Apply any simulated approval override before checking the gate.
            _apply_simulated_approval_override(scenario)

            # Enforce approval state before execution attempt.
            if not _precheck_approval_gate(scenario):
                time.sleep(interval_seconds)
                continue

            base_url = os.environ.get("NRE_AGENT_LATTICE_URL", "http://lattice:8080").strip()
            response = call_lattice(scenario=scenario, base_url=base_url)

            print("[nre_agent] lattice response:", flush=True)
            print(response, flush=True)

            print(_summarize_response(scenario, response), flush=True)
            _handle_policy_outcome(scenario, response)
            _post_execution_bookkeeping(scenario, response)

        except Exception as exc:
            print(
                f"[nre_agent] ts={_utc_now()} loop_error={type(exc).__name__} message={exc}",
                flush=True,
            )

        time.sleep(interval_seconds)
