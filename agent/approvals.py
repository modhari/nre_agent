import json
import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any


def _get_approval_root() -> Path:
    """
    Resolve approval storage directory.

    Priority:
    1. Env override
    2. Local dev default (/tmp)
    """
    root = os.environ.get("NRE_AGENT_APPROVAL_DIR", "/tmp/nre_agent_approvals")
    path = Path(root)

    # Ensure directory exists
    path.mkdir(parents=True, exist_ok=True)

    return path


def _approval_file_path(scenario: str) -> Path:
    """
    Map scenario → file path
    """
    safe_name = scenario.replace(":", "_")
    return _get_approval_root() / f"{safe_name}.json"


def write_approval_record(
    scenario: str,
    status: str,
    risk_level: str,
    blast_radius_score: int,
) -> Dict[str, Any]:
    """
    Persist approval record.

    This is intentionally simple JSON storage for now.
    Later this can move to DB or API.
    """
    record = {
        "scenario": scenario,
        "status": status,
        "risk_level": risk_level,
        "blast_radius_score": blast_radius_score,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }

    path = _approval_file_path(scenario)

    with open(path, "w") as f:
        json.dump(record, f, indent=2)

    return record
