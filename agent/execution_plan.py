from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agent.bgp_decision import BgpDecision, DecisionAction


@dataclass(frozen=True)
class ExecutionStep:
    """
    One step in a future execution plan.

    This does not execute anything.
    It only structures what would happen if execution were later enabled.
    """

    step_id: str
    title: str
    summary: str
    step_type: str
    target: dict[str, Any] = field(default_factory=dict)
    prerequisites: list[str] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    rollback_hint: str | None = None
    approval_required: bool = False
    blocked: bool = False


@dataclass(frozen=True)
class ExecutionPlan:
    """
    Agent side execution plan derived from a BGP decision.

    This is intentionally a planning artifact only.
    The current phase never executes these steps.
    """

    plan_id: str
    incident_id: str
    summary: str
    execution_enabled: bool
    approval_required: bool
    safe_steps: list[ExecutionStep] = field(default_factory=list)
    gated_steps: list[ExecutionStep] = field(default_factory=list)
    skipped_actions: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


def build_execution_plan(decision: BgpDecision) -> ExecutionPlan:
    """
    Convert a validated BGP decision into an execution ready plan shape.

    Current behavior:
    build steps only
    preserve approval boundaries
    never execute

    Later this can be wired to Lattice write paths once approval and execution
    policies are fully defined.
    """
    safe_steps = [_action_to_step(action) for action in decision.safe_actions]
    gated_steps = [_action_to_step(action) for action in decision.gated_actions]

    return ExecutionPlan(
        plan_id=f"plan:{decision.incident_id}",
        incident_id=decision.incident_id,
        summary=f"Execution plan for {decision.summary}",
        execution_enabled=decision.execution_enabled,
        approval_required=decision.approval_required,
        safe_steps=safe_steps,
        gated_steps=gated_steps,
        skipped_actions=list(decision.suppressed_actions),
        metadata={
            "root_cause": decision.root_cause,
            "fabric": decision.fabric,
            "device": decision.device,
        },
    )


def execution_plan_to_dict(plan: ExecutionPlan) -> dict[str, Any]:
    """
    Convert plan to a log friendly dict.
    """
    return {
        "plan_id": plan.plan_id,
        "incident_id": plan.incident_id,
        "summary": plan.summary,
        "execution_enabled": plan.execution_enabled,
        "approval_required": plan.approval_required,
        "safe_steps": [_step_to_dict(step) for step in plan.safe_steps],
        "gated_steps": [_step_to_dict(step) for step in plan.gated_steps],
        "skipped_actions": plan.skipped_actions,
        "metadata": plan.metadata,
    }


def summarize_execution_plan(plan: ExecutionPlan) -> str:
    """
    Compact operator summary for logs.
    """
    return (
        f"[nre_agent] plan_id={plan.plan_id} "
        f"incident_id={plan.incident_id} "
        f"safe_steps={len(plan.safe_steps)} "
        f"gated_steps={len(plan.gated_steps)} "
        f"skipped_actions={len(plan.skipped_actions)} "
        f"approval_required={plan.approval_required} "
        f"execution_enabled={plan.execution_enabled}"
    )


def _action_to_step(action: DecisionAction) -> ExecutionStep:
    """
    Map a normalized decision action to an execution step.

    The mapping stays intentionally simple in this phase so the execution plan
    shape remains easy to reason about and review.
    """
    return ExecutionStep(
        step_id=f"step:{action.action_id}",
        title=action.title,
        summary=action.summary,
        step_type=action.action_type,
        target=dict(action.target),
        prerequisites=list(action.prerequisites),
        commands=list(action.commands),
        rollback_hint=action.rollback_hint,
        approval_required=action.approval_required,
        blocked=action.blocked,
    )


def _step_to_dict(step: ExecutionStep) -> dict[str, Any]:
    return {
        "step_id": step.step_id,
        "title": step.title,
        "summary": step.summary,
        "step_type": step.step_type,
        "target": step.target,
        "prerequisites": step.prerequisites,
        "commands": step.commands,
        "rollback_hint": step.rollback_hint,
        "approval_required": step.approval_required,
        "blocked": step.blocked,
    }
