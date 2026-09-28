"""AgentCore Platform v1.0 — Policy KB nodes (SVC-C2-029).

PolicyKBBuildNode  — onboard_policy mode: build the per-tenant policy KB once.
PolicyLoadNode     — recommend mode: retrieve clauses and structure them
                     deterministically into policy_constraints.

Both depend on the PolicyKB seam (src/services/policy_kb.py) via constructor
injection so tests can supply an in-memory double.
"""

from __future__ import annotations

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.trust_level import TrustLevel
from framework.schemas.agent_status import AgentStatus
from shared.utils.audit_logger import emit_trace_event

from src.services.policy_kb import InMemoryPolicyKB, PolicyKB


class PolicyKBBuildNode(FunctionNode):
    """onboard_policy: chunk + store the tenant's travel-policy documents."""

    # S-1 trust gate: declared explicitly by design, never inherited implicitly.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self, kb: PolicyKB | None = None) -> None:
        super().__init__()
        self._kb: PolicyKB = kb if kb is not None else InMemoryPolicyKB()

    def execute(self, state: dict) -> dict:
        params = state.get("request_params", {}) or {}
        tenant_id = params.get("tenant_id", "")
        docs_text = state.get("validated_input", state.get("user_input", "")) or ""
        if not tenant_id:
            return {"status": AgentStatus.ERROR.value, "error_log": ["PolicyKBBuildNode: tenant_id missing"]}
        if not docs_text.strip():
            return {"status": AgentStatus.ERROR.value, "error_log": ["PolicyKBBuildNode: policy document empty"]}

        result = self._kb.build(tenant_id, [docs_text])
        emit_trace_event("policy_kb_build", {"tenant_id": tenant_id, "chunks": result.get("chunks", 0)}, state)
        return {
            "kb_status": f"built:{result.get('chunks', 0)} chunks",
            "status": AgentStatus.SUCCESS.value,
        }


class PolicyLoadNode(FunctionNode):
    """recommend: retrieve relevant clauses -> deterministic policy_constraints."""

    # S-1 trust gate: declared explicitly by design, never inherited implicitly.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self, kb: PolicyKB | None = None) -> None:
        super().__init__()
        self._kb: PolicyKB = kb if kb is not None else InMemoryPolicyKB()

    def execute(self, state: dict) -> dict:
        params = state.get("request_params", {}) or {}
        tenant_id = params.get("tenant_id", "")
        tier = params.get("tier", "standard")
        destination = params.get("destination", "")
        query = f"{tier} {destination} per-diem approved vendor cancellation"

        clauses = self._kb.retrieve(tenant_id, query) if tenant_id else []
        emit_trace_event("policy_load", {"tenant_id": tenant_id, "clauses": len(clauses)}, state)

        # Deterministic structuring: per_diem from request wins; else left None
        # for downstream to treat as "no cap known" (needs_review, not auto-pass).
        constraints = {
            "tenant_id": tenant_id,
            "tier": tier,
            "per_diem": params.get("per_diem"),
            "clauses": clauses,               # source clause text for citation
            "clause_count": len(clauses),
        }
        return {"policy_constraints": constraints, "status": AgentStatus.SUCCESS.value}
