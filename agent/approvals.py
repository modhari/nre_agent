from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ApprovalRecord:
    """
    Persistent approval record for a scenario or grouped incident.

    The field name remains `scenario` for backward compatibility with the rest of the
    current agent code, even when the key actually represents an incident id.
    """

    scenario: str
    status: str
    risk_level: str
    blast_radius_score: int
    reasons: list[str]
    updated_at: str


def _utc_now() -> str:
    """
    Return a UTC timestamp string for persistence and logs.
    """
    return datetime.now(timezone.utc).isoformat()


def _approval_root() -> Path:
    """
    Resolve the approval storage directory.

    Local default uses /tmp so development works without container volume setup.
    Production and container deployments can override with NRE_AGENT_APPROVAL_DIR.
    """
    root = os.environ.get("NRE_AGENT_APPROVAL_DIR", "/tmp/nre_agent_approvals")
    path = Path(root)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _approval_path(scenario: str) -> Path:
    """
    Map a scenario or incident id to a stable file path.

    Colons and slashes are normalized so the resulting path is portable.
    """
    safe_name = (
        scenario.replace(":", "_")
        .replace("/", "_")
        .replace("\\", "_")
        .replace(" ", "_")
    )
    return _approval_root() / f"{safe_name}.json"


def _from_dict(data: dict[str, Any]) -> ApprovalRecord:
    """
    Convert raw dict data into ApprovalRecord with safe defaults.
    """
    reasons = data.get("reasons", [])
    if not isinstance(reasons, list):
        reasons = []

    return ApprovalRecord(
        scenario=str(data.get("scenario", "")),
        status=str(data.get("status", "pending")),
        risk_level=str(data.get("risk_level", "unknown")),
        blast_radius_score=int(data.get("blast_radius_score", 0)),
        reasons=[str(item) for item in reasons],
        updated_at=str(data.get("updated_at", _utc_now())),
    )


def get_approval_record(scenario: str) -> ApprovalRecord | None:
    """
    Read one approval record if it exists.
    """
    path = _approval_path(scenario)
    if not path.exists():
        return None

    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        return None

    return _from_dict(data)


def create_pending_approval(
    *,
    scenario: str,
    risk_level: str,
    blast_radius_score: int,
    reasons: list[str],
) -> ApprovalRecord:
    """
    Create or overwrite a pending approval record.
    """
    record = ApprovalRecord(
        scenario=scenario,
        status="pending",
        risk_level=risk_level,
        blast_radius_score=blast_radius_score,
        reasons=[str(item) for item in reasons],
        updated_at=_utc_now(),
    )

    _approval_path(scenario).write_text(json.dumps(asdict(record), indent=2))
    return record


def update_approval_status(scenario: str, status: str) -> ApprovalRecord:
    """
    Update approval status for an existing or implicit record.

    If the record does not exist yet, create a minimal one so the transition is still
    represented consistently.
    """
    existing = get_approval_record(scenario)

    if existing is None:
        existing = ApprovalRecord(
            scenario=scenario,
            status=status,
            risk_level="unknown",
            blast_radius_score=0,
            reasons=[],
            updated_at=_utc_now(),
        )
    else:
        existing = ApprovalRecord(
            scenario=existing.scenario,
            status=status,
            risk_level=existing.risk_level,
            blast_radius_score=existing.blast_radius_score,
            reasons=list(existing.reasons),
            updated_at=_utc_now(),
        )

    _approval_path(scenario).write_text(json.dumps(asdict(existing), indent=2))
    return existing


def clear_approval_record(scenario: str) -> None:
    """
    Delete an approval record if it exists.
    """
    path = _approval_path(scenario)
    if path.exists():
        path.unlink()


def summarize_approval_state(scenario: str) -> dict[str, Any] | None:
    """
    Return a compact dict view of the approval record for logs and API responses.
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


def list_approval_records() -> list[ApprovalRecord]:
    """
    List all approval records currently stored.
    """
    records: list[ApprovalRecord] = []

    for path in sorted(_approval_root().glob("*.json")):
        try:
            data = json.loads(path.read_text())
            if isinstance(data, dict):
                records.append(_from_dict(data))
        except Exception:
            # Keep listing resilient even if one file is malformed.
            continue

    return records
