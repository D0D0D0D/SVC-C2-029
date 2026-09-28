"""AgentCore Platform v1.0 — OutputAssembleNode (SVC-C2-029).

Inner terminal node. Assembles the TOP 3-5 recommendations with booking URL,
reference price, remaining-budget hint and compliance note into a structured
result. This is the inner-graph output that get_output() shapes into sub_result.
"""

from __future__ import annotations

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.trust_level import TrustLevel
from framework.schemas.agent_status import AgentStatus
from shared.utils.audit_logger import emit_trace_event


class OutputAssembleNode(FunctionNode):
    """Assemble the final recommendation payload (inner terminal)."""

    # S-1 trust gate: declared explicitly by design, never inherited implicitly.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict) -> dict:
        recs = state.get("ranked_recommendations", []) or []
        params = state.get("request_params", {}) or {}
        per_diem = (state.get("policy_constraints", {}) or {}).get("per_diem")

        items = []
        for r in recs:
            price = r.get("price")
            remaining = (per_diem - price) if (isinstance(per_diem, (int, float)) and isinstance(price, (int, float))) else None
            items.append({
                "rank": r.get("rank"),
                "name": r.get("name"),
                "booking_url": r.get("booking_url"),
                "reference_price": price,
                "remaining_budget_hint": remaining,
                "needs_review": r.get("needs_review"),
                "reason": r.get("reason"),
            })

        # S-4: at least one domain event per node — the framework audit contract
        emit_trace_event("output_assemble", {"recommendation_count": len(items)}, state)

        result = {
            "destination": params.get("destination"),
            "recommendations": items,
            "compliance_notes": state.get("compliance_notes", "needs_review"),
            "disclaimer": "参考価格です。最終的な価格確認・規程適合・予約は予約ページでご確認ください。",
        }
        return {"result": result, "status": AgentStatus.SUCCESS.value}
