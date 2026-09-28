"""AgentCore Platform v1.0 — SoftRerankNode (SVC-C2-029).

LLM value-judgment rerank over the ALREADY-FILTERED candidate set. The node
may reorder and write rationale, but MUST NOT add candidates not present in
filtered_candidates (S-3 later enforces this by ID cross-check).

Value-judgment axes, in priority order (docs/02_design.md L-02):
  1. policy fit (needs_review down-weighted)
  2. effective cost (price incl. rough point-reward sense)
  3. venue proximity
  4. business-trip amenities (大浴場 / breakfast / wifi / laundry)
  5. cancellation flexibility

The ordering here uses a deterministic scorer so behavior is testable; the
LLM seam supplies the human-readable rationale. In production the scorer can be
replaced/augmented by the LLM, but the ID-invariant (output IDs subset of
filtered_candidates) always holds.
"""

from __future__ import annotations

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.trust_level import TrustLevel
from framework.schemas.agent_status import AgentStatus
from shared.utils.audit_logger import emit_trace_event

from src.services.llm_client import EchoLLMClient, LLMClient

_TOP_N = 5
_TRIP_AMENITIES = ("大浴場", "breakfast", "朝食", "wifi", "wi-fi", "laundry", "ランドリー")


class SoftRerankNode(FunctionNode):
    """Rerank filtered candidates by value judgment; generate rationale."""

    # S-1 trust gate: declared explicitly by design, never inherited implicitly.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self, llm: LLMClient | None = None) -> None:
        super().__init__()
        self._llm: LLMClient = llm if llm is not None else EchoLLMClient()

    def _score(self, c: dict) -> float:
        score = 0.0
        # 1. policy fit — needs_review pushed down
        score += -5.0 if c.get("needs_review") else 5.0
        # 2. effective cost — cheaper is better (unknown price neutral)
        price = c.get("price")
        if isinstance(price, (int, float)):
            score += max(0.0, 3.0 - price / 10000.0)
        # 3. venue proximity — nearer is better (unknown neutral)
        dist = c.get("distance_km")
        if isinstance(dist, (int, float)):
            score += max(0.0, 2.0 - dist)
        # 4. business-trip amenities
        amenities = [a.lower() for a in c.get("amenities", []) or []]
        score += sum(0.5 for a in _TRIP_AMENITIES if any(a.lower() in am for am in amenities))
        return score

    def execute(self, state: dict) -> dict:
        candidates = state.get("filtered_candidates", []) or []
        ranked = sorted(candidates, key=self._score, reverse=True)[:_TOP_N]

        rationale = self._llm.complete(self._build_prompt(state, ranked)) if ranked else ""
        emit_trace_event("llm_rerank", {"in": len(candidates), "ranked": len(ranked)}, state)

        recommendations = []
        for rank, c in enumerate(ranked, start=1):
            recommendations.append({
                "id": c.get("id"),
                "rank": rank,
                "name": c.get("name"),
                "vendor": c.get("vendor"),
                "price": c.get("price"),
                "booking_url": c.get("url"),
                "needs_review": bool(c.get("needs_review")),
                "reason": rationale,
            })
        return {"ranked_recommendations": recommendations, "status": AgentStatus.SUCCESS.value}

    def _build_prompt(self, state: dict, ranked: list[dict]) -> str:
        params = state.get("request_params", {}) or {}
        names = ", ".join(str(c.get("name")) for c in ranked)
        # Fall back to a placeholder so the LLM still has context when destination
        # is absent (e.g. the degraded search-page path).
        destination = params.get("destination") or "(目的地未指定)"
        return (
            "You are a corporate-travel hotel concierge. Given the company travel "
            "policy context and these already policy-eligible hotels, briefly explain "
            "why they are recommended, weighting: policy fit > effective cost > venue "
            f"proximity > business-trip amenities > cancellation flexibility.\n"
            f"Destination: {destination}\nHotels: {names}"
        )
