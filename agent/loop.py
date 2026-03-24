from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any

from agent.approvals import (
    clear_approval_record,
    create_pending_approval,
    get_approval_record,
    summarize_approval_state,
    update_approval_status,
)
from agent.client import call_lattice
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


def _cooldown_seconds() -> int:
    """
    Cooldown window after an approved reopen and execution.

    This is intentionally simple.
    Later this can move into persistent state or policy config.
    """
    return int(os.environ.get("NRE_AGENT_APPROVAL_COOLDOWN_SECONDS", "300"))


def _set_cooldown(scenario: str) -> None:
    """
    Start cooldown for a scenario after approval is consumed.
    """
    _APPROVAL_COOLDOWN_UNTIL[scenario] = _utc_now_dt() + timedelta(seconds=_cooldown_seconds())


def _get_cooldown_remaining_seconds(scenario: str) -> int:
    """
    Return remaining cooldown seconds for a scenario.
    """
    expires = _APPROVAL_COOLDOWN_UNTIL.get(scenario)
    if expires is None:
        return 0

    remaining = int((expires - _utc_now_dt()).total_seconds())
    return max(remaining, 0)


def _is_in_cooldown(scenario: str) -> bool:
    """
    Check whether the scenario is still in cooldown.
    """
    return _get_cooldown_remaining_seconds(scenario) > 0


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
    Build a concise summary line for each loop iteration.
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


def _apply_simulated_approval_override(scenario: str) -> None:
    """
    Apply an optional approval override before gate enforcement.

    This matters because the approval gate may otherwise hold
    the scenario before the loop reaches policy handling logic.
    """
    simulated_status = os.environ.get("NRE_AGENT_APPROVAL_STATUS", "").strip().lower()
    if simulated_status not in {"approved", "rejected"}:
        return

    record = get_approval_record(scenario)
    if record is None:
        return

    if record.status == simulated_status:
        return

    updated = update_approval_status(scenario, simulated_status)
    print(
        f"[nre_agent] ts={_utc_now()} scenario={scenario} "
        f"approval_transition={updated.status}",
        flush=True,
    )


def _precheck_approval_gate(scenario: str) -> bool:
    """
    Enforce approval state before calling lattice.

    Returns True when the agent is allowed to proceed.
    Returns False when the scenario should be skipped for now.

    Rules:
    pending   -> hold
    rejected  -> suppress
    approved  -> reopen and proceed once
    cooldown  -> skip repeated approval churn
    no record -> proceed
    """
    if _is_in_cooldown(scenario):
        remaining = _get_cooldown_remaining_seconds(scenario)
        print(
            f"[nre_agent] ts={_utc_now()} scenario={scenario} "
            f"approval_gate=cooldown remaining_seconds={remaining}",
            flush=True,
        )
        return False

    record = get_approval_record(scenario)
    if record is None:
        return True

    if record.status == "pending":
        print(
            f"[nre_agent] ts={_utc_now()} scenario={scenario} "
            f"approval_gate=hold approval_status=pending",
            flush=True,
        )
        return False

    if record.status == "rejected":
        print(
            f"[nre_agent] ts={_utc_now()} scenario={scenario} "
            f"approval_gate=suppress approval_status=rejected",
            flush=True,
        )
        return False

    if record.status == "approved":
        print(
            f"[nre_agent] ts={_utc_now()} scenario={scenario} "
            f"approval_gate=reopen approval_status=approved",
            flush=True,
        )

        # Clear the approval record so the scenario can proceed.
        # The next high risk result will not immediately recreate a
        # pending record because cooldown is applied after execution.
        clear_approval_record(scenario)

        print(
            f"[nre_agent] ts={_utc_now()} scenario={scenario} approval_gate=proceed",
            flush=True,
        )
        return True

    return True


def _handle_policy_outcome(scenario: str, response: dict[str, Any]) -> None:
    """
    Policy aware agent behavior.

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
    Apply post execution bookkeeping.

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


def run_agent_loop() -> None:
    """
    Continuous outer agent loop.

    Responsibilities:
    choose scenario
    apply approval override before gate check
    enforce approval gate
    call lattice
    summarize result
    react to policy outcome
    apply post execution bookkeeping
    sleep and repeat
    """
    interval_seconds = int(os.environ.get("NRE_AGENT_INTERVAL_SECONDS", "30"))

    print(f"[nre_agent] starting loop with interval={interval_seconds}s", flush=True)

    while True:
        try:
            scenario = get_next_scenario()

            print(f"[nre_agent] selected scenario: {scenario}", flush=True)

            # Apply any simulated approval override before checking the gate.
            _apply_simulated_approval_override(scenario)

            # Enforce approval state before execution attempt.
            if not _precheck_approval_gate(scenario):
                time.sleep(interval_seconds)
                continue

            response = call_lattice(scenario=scenario)

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
