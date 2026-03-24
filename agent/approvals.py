from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ApprovalRecord:
    """
    Simple approval record stored as JSON.

    status
    pending, approved, or rejected

    scenario
    Scenario that triggered the approval flow

    created_at
    UTC timestamp when the request was created

    updated_at
    UTC timestamp for the most recent status update

    risk_level
    Risk level returned by lattice and MCP

    blast_radius_score
    Numeric blast radius returned by lattice and MCP

    reasons
    Human readable reasons for the escalation
    """

    status: str
    scenario: str
    created_at: str
    updated_at: str
    risk_level: str
    blast_radius_score: int
    reasons: list[str]


def _utc_now() -> str:
    """
    Return UTC timestamp as ISO string.
    """
    return datetime.now(timezone.utc).isoformat()


def _approval_dir() -> Path:
    """
    Directory where approval records are stored.

    This is intentionally file based for a first working version.
    Later this can move to:
    Kubernetes resources
    Redis
    Postgres
    workflow engine
    """
    root = os.environ.get("NRE_AGENT_APPROVAL_DIR", "/tmp/nre_agent_approvals")
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _approval_file(scenario: str) -> Path:
    """
    Build a stable path for a scenario approval file.
    """
    safe_name = scenario.replace("/", "_")
    return _approval_dir() / f"{safe_name}.json"


def get_approval_record(scenario: str) -> ApprovalRecord | None:
    """
    Load approval record for a scenario if it exists.
    """
    path = _approval_file(scenario)
    if not path.exists():
        return None

    data = json.loads(path.read_text())
    return ApprovalRecord(
        status=str(data["status"]),
        scenario=str(data["scenario"]),
        created_at=str(data["created_at"]),
        updated_at=str(data["updated_at"]),
        risk_level=str(data["risk_level"]),
        blast_radius_score=int(data["blast_radius_score"]),
        reasons=[str(x) for x in data.get("reasons", [])],
    )


def create_pending_approval(
    scenario: str,
    risk_level: str,
    blast_radius_score: int,
    reasons: list[str],
) -> ApprovalRecord:
    """
    Create a pending approval record unless one already exists.
    """
    existing = get_approval_record(scenario)
    if existing is not None:
        return existing

    now = _utc_now()
    record = ApprovalRecord(
        status="pending",
        scenario=scenario,
        created_at=now,
        updated_at=now,
        risk_level=risk_level,
        blast_radius_score=blast_radius_score,
        reasons=reasons,
    )

    _approval_file(scenario).write_text(json.dumps(asdict(record), indent=2))
    return record


def update_approval_status(scenario: str, status: str) -> ApprovalRecord:
    """
    Update an approval record to approved or rejected.
    """
    current = get_approval_record(scenario)
    if current is None:
        raise ValueError(f"no approval record found for scenario={scenario}")

    if status not in {"pending", "approved", "rejected"}:
        raise ValueError(f"unsupported approval status={status}")

    record = ApprovalRecord(
        status=status,
        scenario=current.scenario,
        created_at=current.created_at,
        updated_at=_utc_now(),
        risk_level=current.risk_level,
        blast_radius_score=current.blast_radius_score,
        reasons=current.reasons,
    )

    _approval_file(scenario).write_text(json.dumps(asdict(record), indent=2))
    return record


def clear_approval_record(scenario: str) -> None:
    """
    Remove approval record for a scenario.
    """
    path = _approval_file(scenario)
    if path.exists():
        path.unlink()


def summarize_approval_state(scenario: str) -> dict[str, Any] | None:
    """
    Return a small dict summary for operator logging.
    """
    record = get_approval_record(scenario)
    if record is None:
        return None

    return {
        "scenario": record.scenario,
        "status": record.status,
        "risk_level": record.risk_level,
        "blast_radius_score": record.blast_radius_score,
        "updated_at": record.updated_at,
    }
