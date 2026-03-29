from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.approvals import ApprovalRecord
from agent.execution_plan import ExecutionPlan


@dataclass(frozen=True)
class PlanState:
    """
    Agent side plan state for a grouped incident or scenario.

    This state machine is intentionally simple for this phase.
    It gives the agent a clean lifecycle without enabling execution.

    States:
    draft
    pending_approval
    approved_execution_disabled
    rejected
    ready_no_approval
    """

    incident_id: str
    state: str
    summary: str
    execution_blocked: bool
    next_step: str
    approval_status: str | None = None
    reasons: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def build_plan_state(
    *,
    plan: ExecutionPlan,
    approval_record: ApprovalRecord | None,
) -> PlanState:
    """
    Build the current state for a plan.

    Rules:
    1. If approval is not required, the plan is ready without approval.
    2. If approval is required and there is no record yet, the plan is still draft.
    3. If approval is pending, the plan waits for approval.
    4. If approval is rejected, the plan is rejected.
    5. If approval is approved but execution is disabled, hold the plan in an
       approved but blocked state.
    """
    if not plan.approval_required:
        return PlanState(
            incident_id=plan.incident_id,
            state="ready_no_approval",
            summary="Plan does not require approval",
            execution_blocked=not plan.execution_enabled,
            next_step=(
                "Execution remains disabled until a later phase enables execution"
                if not plan.execution_enabled
                else "Plan could proceed without approval"
            ),
            approval_status=None,
            metadata=_plan_metadata(plan),
        )

    if approval_record is None:
        return PlanState(
            incident_id=plan.incident_id,
            state="draft",
            summary="Plan requires approval but no approval record exists yet",
            execution_blocked=True,
            next_step="Create a pending approval record",
            approval_status=None,
            metadata=_plan_metadata(plan),
        )

    if approval_record.status == "pending":
        return PlanState(
            incident_id=plan.incident_id,
            state="pending_approval",
            summary="Plan is waiting for approval",
            execution_blocked=True,
            next_step="Wait for approval decision",
            approval_status=approval_record.status,
            reasons=list(approval_record.reasons),
            metadata=_plan_metadata(plan),
        )

    if approval_record.status == "rejected":
        return PlanState(
            incident_id=plan.incident_id,
            state="rejected",
            summary="Plan approval was rejected",
            execution_blocked=True,
            next_step="Keep the plan blocked and review the rejection",
            approval_status=approval_record.status,
            reasons=list(approval_record.reasons),
            metadata=_plan_metadata(plan),
        )

    if approval_record.status == "approved":
        return PlanState(
            incident_id=plan.incident_id,
            state="approved_execution_disabled",
            summary="Plan was approved but execution is still disabled",
            execution_blocked=True,
            next_step="Hold until a later phase enables execution",
            approval_status=approval_record.status,
            reasons=list(approval_record.reasons),
            metadata=_plan_metadata(plan),
        )

    return PlanState(
        incident_id=plan.incident_id,
        state="draft",
        summary="Plan state could not be resolved cleanly",
        execution_blocked=True,
        next_step="Inspect approval record and plan metadata",
        approval_status=approval_record.status,
        reasons=list(approval_record.reasons),
        metadata=_plan_metadata(plan),
    )


def plan_state_to_dict(plan_state: PlanState) -> dict[str, Any]:
    """
    Convert plan state to a log friendly dict.
    """
    return {
        "incident_id": plan_state.incident_id,
        "state": plan_state.state,
        "summary": plan_state.summary,
        "execution_blocked": plan_state.execution_blocked,
        "next_step": plan_state.next_step,
        "approval_status": plan_state.approval_status,
        "reasons": plan_state.reasons,
        "metadata": plan_state.metadata,
    }


def summarize_plan_state(plan_state: PlanState) -> str:
    """
    Compact operator summary for logs.
    """
    return (
        f"[nre_agent] incident_id={plan_state.incident_id} "
        f"plan_state={plan_state.state} "
        f"approval_status={plan_state.approval_status} "
        f"execution_blocked={plan_state.execution_blocked} "
        f'next_step="{plan_state.next_step}"'
    )


def _plan_metadata(plan: ExecutionPlan) -> dict[str, Any]:
    """
    Extract compact plan metadata for state reporting.
    """
    return {
        "plan_id": plan.plan_id,
        "safe_step_count": len(plan.safe_steps),
        "gated_step_count": len(plan.gated_steps),
        "skipped_action_count": len(plan.skipped_actions),
        "execution_enabled": plan.execution_enabled,
        "approval_required": plan.approval_required,
    }
