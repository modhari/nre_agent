from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from typing import Any

from agent.client import call_lattice
from agent.scenarios import get_next_scenario


def _utc_now() -> str:
    """
    Return UTC timestamp for operator friendly logs.
    """
    return datetime.now(timezone.utc).isoformat()


def _extract_risk(response: dict[str, Any]) -> dict[str, Any] | None:
    """
    Pull the risk block out of the lattice response.
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
    Build a concise operator summary line.
    """
    status = str(response.get("status", "unknown"))
    message = str(response.get("message", ""))

    risk = _extract_risk(response)
    if risk is None:
        return (
            f"[nre_agent] ts={_utc_now()} "
            f"scenario={scenario} "
            f"status={status} "
            f'message="{message}"'
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
        f'message="{message}"'
    )


def _handle_policy_outcome(scenario: str, response: dict[str, Any]) -> None:
    """
    Minimal policy-aware agent behavior.

    Current behavior:
    - low risk: continue
    - medium risk: log caution
    - approval required or high risk: log escalation

    Later this can evolve into:
    - Slack / email approval workflow
    - change window checks
    - suppression rules
    - auto-pause
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
    reasons = risk.get("reasons", [])

    if requires_approval or risk_level == "high":
        print(
            f"[nre_agent] ts={_utc_now()} scenario={scenario} "
            f"policy_action=escalate reasons={reasons}",
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
    - choose scenario
    - call lattice
    - summarize result
    - react to policy outcome
    - sleep and repeat
    """
    interval_seconds = int(os.environ.get("NRE_AGENT_INTERVAL_SECONDS", "30"))

    print(f"[nre_agent] starting loop with interval={interval_seconds}s", flush=True)

    while True:
        try:
            scenario = get_next_scenario()

            print(f"[nre_agent] selected scenario: {scenario}", flush=True)

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
