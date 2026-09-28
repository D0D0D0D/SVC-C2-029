"""AgentCore Platform v1.0 — SVC-C2-029 outer graph.

Cat 2: outer AgentBaseGraph (fixed 5-node backbone) + a GraphNode in the `main`
slot wrapping the inner HotelRecommendationWorkflow (src/graph/domain_workflow_graph.py).

  START → initialize → pre_process → main → {route} → post_process → finalize → END

Do NOT override add_edges() — backbone wiring belongs to the framework.
"""

from __future__ import annotations

import json
from typing import ClassVar

from framework.graph.agent_base_graph import AgentBaseGraph
from framework.nodes.graph_node import GraphNode
from framework.schemas.agent_state import AgentState

from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.schemas.state import State


class HotelRecommendationGraphNode(GraphNode):
    """`main` slot — wraps the inner HotelRecommendationWorkflow."""

    # Unhandled inner exceptions re-raise as SubgraphError (fail fast). Handled
    # failures (web fetch fail / 0 results) are degraded inside WebFetchNode.
    error_strategy: ClassVar[str] = "propagate"
    propagate_hitl: ClassVar[bool] = False

    def get_subgraph(self):
        # Production services (KB / web search / LLM) are threaded to the inner
        # nodes via config keys "kb"/"search"/"llm" — HotelRecommendationWorkflow
        # resolves ctor arg > config > stub (M-01 fix). To wire real backends,
        # put them in the agent config under those keys.
        from src.graph.domain_workflow_graph import HotelRecommendationWorkflow
        cfg = getattr(self, "config", None)
        return HotelRecommendationWorkflow(config=cfg)

    def extract_input(self, state: AgentState) -> str:
        """Pack request_params as JSON into the string user_input.

        The GraphNode contract passes only a string into inner invoke(); the
        inner _IngestNode unpacks it. policy_document carries the policy text for
        onboard_policy mode.
        """
        payload = {
            "request_params": state.get("request_params", {}),
            "policy_document": state.get("validated_input", state.get("user_input", "")),
        }
        return json.dumps(payload, ensure_ascii=False, default=str)

    def merge_output(self, state: AgentState, sub_result: dict) -> dict:
        """Surface only the keys present in sub_result (mode-dependent).

        onboard_policy -> {kb_status, status}
        recommend      -> {filtered_candidates, ranked_recommendations,
                           compliance_notes, result, status}
        """
        delta: dict = {}
        for key in (
            "kb_status",
            "filtered_candidates",
            "ranked_recommendations",
            "compliance_notes",
            "result",
            "status",
        ):
            if key in sub_result:
                delta[key] = sub_result[key]
        return delta


class Graph(AgentBaseGraph):
    """SVC-C2-029 outer graph — Business Travel Hotel & Plan Recommendation Agent."""

    @property
    def name(self) -> str:
        return "business_travel_hotel_agent"

    @property
    def state_schema(self) -> type:
        return State

    def register_nodes(self) -> None:
        super().register_nodes()  # injects initialize + finalize
        self._nodes["pre_process"] = PreProcessNode()
        self._nodes["main"] = HotelRecommendationGraphNode()
        self._nodes["post_process"] = PostProcessNode()

    # add_edges() is NOT overridden — backbone wiring belongs to the framework.

    def get_output(self, state: State) -> dict:
        """Extend the default projection with audit-relevant fields.

        Surfaces filtered_candidates + compliance_notes so the S-3 ID-crosscheck
        invariant is externally auditable, in addition to the
        default {output, status, trace_id, correlation_id, node_history}.
        """
        out = super().get_output(state)
        out["filtered_candidates"] = state.get("filtered_candidates", [])
        out["compliance_notes"] = state.get("compliance_notes")
        return out
