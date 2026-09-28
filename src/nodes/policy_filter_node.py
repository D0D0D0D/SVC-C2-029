"""AgentCore Platform v1.0 — PolicyFilterNode (SVC-C2-029).

Deterministic FIRST-PASS filter (not a compliance guarantee). Drops only
clearly-out-of-policy candidates:
  - vendor not in approved_vendors (when an approved list is defined)
  - price clearly over the per-diem cap (with a tolerance margin)
Candidates whose compliance cannot be determined (unknown price, no cap known,
degraded search-page) are NOT dropped — they are kept and tagged needs_review.
Final price-check and booking are the user's job at the destination URL.
"""

from __future__ import annotations

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.trust_level import TrustLevel
from framework.schemas.agent_status import AgentStatus
from shared.utils.audit_logger import emit_trace_event

# Candidates may exceed the per-diem cap by this fraction before being dropped
# outright — the filter only removes CLEAR violations; grey cases are kept as
# needs_review for the user to judge at the URL.
_DEFAULT_PER_DIEM_HARD_MARGIN = 1.15


class PolicyFilterNode(FunctionNode):
    """First-pass deterministic policy filter.

    The per-diem hard-drop margin is tenant-tunable: pass it at construction, or
    override per request via policy_constraints["per_diem_hard_margin"].
    """

    # S-1 trust gate: declared explicitly by design, never inherited implicitly.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self, per_diem_hard_margin: float = _DEFAULT_PER_DIEM_HARD_MARGIN) -> None:
        super().__init__()
        self._margin = per_diem_hard_margin

    def execute(self, state: dict) -> dict:
        raw = state.get("raw_candidates", []) or []
        constraints = state.get("policy_constraints", {}) or {}
        per_diem = constraints.get("per_diem")
        approved = constraints.get("approved_vendors")  # None => no vendor restriction known
        margin = constraints.get("per_diem_hard_margin", self._margin)

        kept: list[dict] = []
        notes: list[dict] = []

        for c in raw:
            cid = c.get("id")
            vendor = c.get("vendor")
            price = c.get("price")

            # Clear vendor violation (only when an approved list is defined)
            if approved and vendor is not None and vendor not in approved and not c.get("degraded"):
                notes.append({"id": cid, "action": "dropped", "reason": "vendor_not_approved"})
                continue

            # Clear per-diem violation (only when both cap and price are known)
            if per_diem and isinstance(price, (int, float)) and price > per_diem * margin:
                notes.append({"id": cid, "action": "dropped", "reason": "over_per_diem"})
                continue

            # Undeterminable compliance -> keep, tag needs_review
            item = dict(c)
            if c.get("degraded") or price is None or per_diem is None:
                item["needs_review"] = True
                notes.append({"id": cid, "action": "kept", "reason": "needs_review"})
            else:
                item["needs_review"] = price > per_diem  # within margin but over nominal cap
            kept.append(item)

        any_review = any(k.get("needs_review") for k in kept) or not kept
        compliance_notes = "needs_review" if any_review else "compliant"

        emit_trace_event(
            "policy_filter",
            {"in": len(raw), "kept": len(kept), "dropped": len(raw) - len(kept)},
            state,
        )
        return {
            "filtered_candidates": kept,
            "filter_notes": notes,
            "compliance_notes": compliance_notes,
            "status": AgentStatus.SUCCESS.value,
        }
