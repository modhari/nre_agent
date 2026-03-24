from __future__ import annotations

from dataclasses import asdict

from fastapi import FastAPI, HTTPException

from agent.approvals import (
    ApprovalRecord,
    get_approval_record,
    list_approval_records,
    update_approval_status,
)

app = FastAPI(title="nre-agent-approvals", version="0.1.0")


@app.get("/")
def root() -> dict[str, str]:
    """
    Basic service identity endpoint.
    """
    return {
        "service": "nre-agent-approvals",
        "status": "running",
    }


@app.get("/approvals")
def get_approvals() -> dict:
    """
    List all approval records.
    """
    records = [asdict(record) for record in list_approval_records()]
    return {"approvals": records}


@app.get("/approvals/{scenario}")
def get_approval(scenario: str) -> dict:
    """
    Read a single approval record by scenario name.
    """
    record = get_approval_record(scenario)
    if record is None:
        raise HTTPException(status_code=404, detail=f"approval not found for scenario={scenario}")

    return asdict(record)


@app.post("/approvals/{scenario}/approve")
def approve_scenario(scenario: str) -> dict:
    """
    Mark a scenario approved.
    """
    try:
        record: ApprovalRecord = update_approval_status(scenario, "approved")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "status": "ok",
        "action": "approved",
        "approval": asdict(record),
    }


@app.post("/approvals/{scenario}/reject")
def reject_scenario(scenario: str) -> dict:
    """
    Mark a scenario rejected.
    """
    try:
        record: ApprovalRecord = update_approval_status(scenario, "rejected")
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return {
        "status": "ok",
        "action": "rejected",
        "approval": asdict(record),
    }
