"""AgentCore Platform v1.0 — WebFetchNode (SVC-C2-029).

Cross-site hotel fetch via the HotelSearch seam. On fetch failure or zero
results it does NOT raise (layer-1 handled failure): it appends a single
degraded search-page-URL candidate (degraded=True) so the pipeline continues
and the user still gets a usable link. Unexpected exceptions propagate to the
GraphNode (layer-2, error_strategy=propagate).
"""

from __future__ import annotations

from typing import ClassVar

from framework.nodes.function_node import FunctionNode
from framework.schemas.trust_level import TrustLevel
from framework.schemas.agent_status import AgentStatus
from shared.utils.audit_logger import emit_trace_event

from src.services.web_search import HotelSearch, StaticHotelSearch


class WebFetchNode(FunctionNode):
    """Fetch hotel candidates across sites; degrade to a search-page URL."""

    # S-1 trust gate: declared explicitly by design, never inherited implicitly.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def __init__(self, search: HotelSearch | None = None) -> None:
        super().__init__()
        self._search: HotelSearch = search if search is not None else StaticHotelSearch()

    def execute(self, state: dict) -> dict:
        params = state.get("request_params", {}) or {}

        try:
            candidates = self._search.search(params) or []
        except Exception as exc:  # layer-1: handled failure, do not raise
            candidates = []
            emit_trace_event("web_fetch", {"ok": False, "error": type(exc).__name__}, state)

        if not candidates:
            url = self._search.search_page_url(params)
            candidates = [{
                "id": "degraded-search-page",
                "name": f"検索ページ（{params.get('destination', '')}）",
                "vendor": "search-page",
                "price": None,
                "url": url,
                "amenities": [],
                "distance_km": None,
                "degraded": True,
            }]
            emit_trace_event("web_fetch", {"ok": True, "degraded": True, "count": 0}, state)
        else:
            emit_trace_event("web_fetch", {"ok": True, "degraded": False, "count": len(candidates)}, state)

        return {"raw_candidates": candidates, "status": AgentStatus.SUCCESS.value}
