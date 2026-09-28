"""AgentCore Platform v1.0 — HotelRecommendationWorkflow inner graph (SVC-C2-029).

Inner graph for the Cat 2 template, instantiated by
HotelRecommendationGraphNode.get_subgraph() in graph.py.

Topology (2 modes via add_conditional_edges, docs/02_design.md M-01):

  START → route_by_mode ─┬─[onboard_policy]→ policy_kb_build ─────────────────→ END
                         └─[recommend]────→ policy_load → web_fetch →
                                            policy_filter → soft_rerank →
                                            output_assemble → END

The GraphNode passes only a string user_input into invoke(); request_params are
packed as JSON by the outer extract_input() and unpacked here in _ingest.
get_output() shapes sub_result per mode for the outer merge_output().
"""

from __future__ import annotations

import json
from typing import ClassVar

from langgraph.graph import END, START

from framework.graph.base_graph import BaseGraph
from framework.nodes.function_node import FunctionNode
from framework.schemas.trust_level import TrustLevel
from framework.schemas.agent_status import AgentStatus
from shared.utils.audit_logger import emit_trace_event

from src.nodes.output_assemble_node import OutputAssembleNode
from src.nodes.policy_filter_node import PolicyFilterNode
from src.nodes.policy_nodes import PolicyKBBuildNode, PolicyLoadNode
from src.nodes.soft_rerank_node import SoftRerankNode
from src.nodes.web_fetch_node import WebFetchNode
from src.schemas.state import State


class _IngestNode(FunctionNode):
    """Unpack the JSON-packed request_params from the string user_input.

    The GraphNode contract passes only user_input (str) into the inner graph,
    so the outer extract_input() JSON-encodes request_params and this node
    restores request_params / validated_input into the inner state.
    """

    # S-1 trust gate: declared explicitly by design, never inherited implicitly.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict) -> dict:
        raw = state.get("user_input", "") or ""
        params: dict = {}
        policy_document = raw
        try:
            decoded = json.loads(raw)
            if isinstance(decoded, dict):
                params = decoded.get("request_params", {}) or {}
                policy_document = decoded.get("policy_document", raw)
        except (ValueError, TypeError):
            params = {}
        # S-4: at least one domain event per node — the framework audit contract
        emit_trace_event("ingest_unpack", {"has_request_params": bool(params)}, state)
        return {
            "request_params": params,
            "validated_input": policy_document,
            "status": AgentStatus.SUCCESS.value,
        }


class HotelRecommendationWorkflow(BaseGraph):
    """Inner multi-step domain workflow with 2-mode conditional routing."""

    def __init__(self, config: dict | None = None,
                 kb=None, search=None, llm=None) -> None:
        # DI seams. Resolution order (M-01 fix): explicit ctor arg (tests inject
        # doubles) > config["kb"|"search"|"llm"] (production services threaded from
        # the outer Graph via GraphNode.get_subgraph) > None (fallback stub in the
        # node). This ensures production services actually reach the inner nodes
        # instead of silently running on stubs.
        cfg = config or {}
        self._kb = kb if kb is not None else cfg.get("kb")
        self._search = search if search is not None else cfg.get("search")
        self._llm = llm if llm is not None else cfg.get("llm")
        super().__init__(config=config)

    @property
    def name(self) -> str:
        return "HotelRecommendationWorkflow"

    @property
    def state_schema(self) -> type:
        return State

    def _validate_config(self) -> None:
        pass

    def register_nodes(self) -> None:
        # No super() — BaseGraph.register_nodes() is abstract. Do NOT register
        # initialize/finalize (outer backbone concern).
        self._nodes["ingest"] = _IngestNode()
        self._nodes["policy_kb_build"] = PolicyKBBuildNode(kb=self._kb)
        self._nodes["policy_load"] = PolicyLoadNode(kb=self._kb)
        self._nodes["web_fetch"] = WebFetchNode(search=self._search)
        self._nodes["policy_filter"] = PolicyFilterNode()
        self._nodes["soft_rerank"] = SoftRerankNode(llm=self._llm)
        self._nodes["output_assemble"] = OutputAssembleNode()

    def add_edges(self) -> None:
        # ingest first (unpack params), then branch by mode.
        self._sg.add_edge(START, "ingest")
        self._sg.add_conditional_edges(
            "ingest",
            self.route,
            {"onboard_policy": "policy_kb_build", "recommend": "policy_load"},
        )
        # onboard branch
        self._sg.add_edge("policy_kb_build", END)
        # recommend branch (linear)
        self._sg.add_edge("policy_load", "web_fetch")
        self._sg.add_edge("web_fetch", "policy_filter")
        self._sg.add_edge("policy_filter", "soft_rerank")
        self._sg.add_edge("soft_rerank", "output_assemble")
        self._sg.add_edge("output_assemble", END)

    def route(self, state: State) -> str:
        # NOTE: annotate with State (not AgentState) so LangGraph projects the
        # FULL State schema into the branch — request_params is a State field, not
        # an AgentState field; annotating AgentState hides it and mis-routes.
        mode = (state.get("request_params", {}) or {}).get("mode", "recommend")
        return "onboard_policy" if mode == "onboard_policy" else "recommend"

    def get_output(self, state: State) -> dict:
        mode = (state.get("request_params", {}) or {}).get("mode", "recommend")
        if mode == "onboard_policy":
            return {"kb_status": state.get("kb_status"), "status": state.get("status")}
        return {
            "filtered_candidates": state.get("filtered_candidates", []),
            "ranked_recommendations": state.get("ranked_recommendations", []),
            "compliance_notes": state.get("compliance_notes", "needs_review"),
            "result": state.get("result", {}),
            "status": state.get("status"),
        }
