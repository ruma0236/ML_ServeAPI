from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


AgentRunStatus = Literal[
    "queued",
    "running",
    "pending_approval",
    "resuming",
    "succeeded",
    "failed",
    "cancelled",
]
ToolCallStatus = Literal[
    "proposed",
    "pending_approval",
    "approved",
    "rejected",
    "executing",
    "succeeded",
    "failed",
]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentStep(ContractModel):
    step_id: str
    node_name: str
    status: AgentRunStatus
    attempt: int = Field(ge=1)
    started_at: str
    finished_at: str | None = None
    checkpoint_uri: str
    input_digest: str
    output_digest: str | None = None
    error_code: str | None = None


class ToolCallAudit(ContractModel):
    call_id: str
    step_id: str
    tool_name: str
    operation_category: Literal["read", "write", "execute", "deploy"]
    side_effect_level: Literal["none", "reversible", "irreversible"]
    status: ToolCallStatus
    actor: str
    arguments_digest: str
    result_digest: str | None = None
    approval_required: bool
    approved_by: str | None = None
    requested_at: str
    completed_at: str | None = None
    evidence_uri: str | None = None
    error_code: str | None = None

    @model_validator(mode="after")
    def approval_is_recorded_for_side_effects(self):
        if self.operation_category != "read" and not self.approval_required:
            raise ValueError("side-effecting tool calls require approval")
        if self.status in {"approved", "executing", "succeeded"} and self.approval_required:
            if not self.approved_by:
                raise ValueError("approved side-effecting tool calls require approved_by")
        if self.status == "failed" and not self.error_code:
            raise ValueError("failed tool calls require error_code")
        return self


class HumanInterrupt(ContractModel):
    interrupt_id: str
    call_id: str
    status: Literal["pending", "approved", "edited", "rejected"]
    allowed_decisions: list[Literal["approve", "edit", "reject"]]
    requested_at: str
    decided_at: str | None = None
    decided_by: str | None = None
    reason: str


class RecoveryAttempt(ContractModel):
    recovery_id: str
    failed_step_id: str
    checkpoint_uri: str
    strategy: Literal["resume_checkpoint", "replay_failed_node", "manual_abort"]
    idempotency_key: str
    attempt: int = Field(ge=1)
    actor: str
    started_at: str
    finished_at: str | None = None
    outcome: Literal["running", "succeeded", "failed", "aborted"]
    evidence_uri: str


class AgentRun(ContractModel):
    schema_version: Literal["evm.agentops.run.v1"]
    run_id: str
    thread_id: str
    tenant_id: str
    environment: Literal["dev", "test", "staging", "production"]
    status: AgentRunStatus
    agent_version: str
    graph_version: str
    model_ref: str
    input_digest: str
    checkpoint_uri: str
    audit_uri: str
    redaction_policy: str
    started_at: str
    updated_at: str
    steps: list[AgentStep] = Field(min_length=1)
    tool_calls: list[ToolCallAudit] = Field(default_factory=list)
    interrupts: list[HumanInterrupt] = Field(default_factory=list)
    recovery_attempts: list[RecoveryAttempt] = Field(default_factory=list)
    automatic_deployment: bool = False
    automatic_model_promotion: bool = False

    @model_validator(mode="after")
    def pending_runs_have_a_pending_interrupt(self):
        if self.status == "pending_approval" and not any(
            item.status == "pending" for item in self.interrupts
        ):
            raise ValueError("pending_approval requires a pending human interrupt")
        return self
