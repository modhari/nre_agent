from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

from agent.approvals import (
    create_pending_approval,
    get_approval_record,
    summarize_approval_state,
    update_approval_status,
)
from agent.client import call_lattice
from agent.scenarios import get_next_scenario


def _utc_now() -> str:
    """
    Return UTC timestamp for operator friendly logs.
    """
    return datetime.now(timezone.utc).isoformat()


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


def _precheck_approval_gate(scenario: str) -> bool:
    """
    Enforce approval state before calling lattice.

    Returns True when the agent is allowed to proceed.
    Returns False when the scenario should be skipped for now.

    Rules:
    pending   -> hold
    rejected  -> suppress
    approved  -> proceed
    no record -> proceed
    """
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
            f"approval_gate=open approval_status=approved",
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
    create or reuse approval record
    enforce approval transitions
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

        # Optional test override for simulated approval or rejection.
        simulated_status = os.environ.get("NRE_AGENT_APPROVAL_STATUS", "").strip().lower()
        if simulated_status in {"approved", "rejected"}:
            updated = update_approval_status(scenario, simulated_status)
            print(
                f"[nre_agent] ts={_utc_now()} scenario={scenario} "
                f"approval_transition={updated.status}",
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


def run_agent_loop() -> None:
    """
    Continuous outer agent loop.

    Responsibilities:
    choose scenario
    enforce approval gate before high risk retries
    call lattice
    summarize result
    react to policy outcome
    sleep and repeat
    """
    interval_seconds = int(os.environ.get("NRE_AGENT_INTERVAL_SECONDS", "30"))

    print(f"[nre_agent] starting loop with interval={interval_seconds}s", flush=True)

    while True:
        try:
            scenario = get_next_scenario()

            print(f"[nre_agent] selected scenario: {scenario}", flush=True)

            # -----------------------------------------------------
            # Approval enforcement before execution attempt
            # -----------------------------------------------------
            if not _precheck_approval_gate(scenario):
                time.sleep(interval_seconds)
                continue

            response = call_lattice(scenario=scenario)

            print("[nre_agent] lattice response:", flush=True)
            print(response, flush=True)

            print(_summarize_response(scenario, response), flush=True)
            _handle_policy_outcome(scenario, response)

        except Exception as exc:
            print(
                f"[nre_agent] ts={_utc_now()} loop_error={type(exc).__name__} message={exc}",
                flush=True,
            )

        time.sleep(interval_seconds)
