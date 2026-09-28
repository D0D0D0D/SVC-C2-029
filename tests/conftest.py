"""Live LLM check fixtures for SVC-C2-029."""

from __future__ import annotations

import pytest


class _PromptLLMAdapter:
    """Adapt the live-check BaseLLM.complete(messages) to complete(prompt)."""

    def __init__(self, base_llm) -> None:
        self._base_llm = base_llm

    def complete(self, prompt: str) -> str:
        response = self._base_llm.complete([{"role": "user", "content": prompt}])
        return response.get("content", "") if isinstance(response, dict) else str(response)


@pytest.fixture
def agent_config(monkeypatch) -> dict:
    from src.graph.graph import Graph, HotelRecommendationGraphNode
    from src.nodes.soft_rerank_node import SoftRerankNode
    from src.services.policy_kb import InMemoryPolicyKB
    from src.services.web_search import StaticHotelSearch

    original_register_nodes = Graph.register_nodes

    def patched_register_nodes(self):
        original_register_nodes(self)
        main = self._nodes.get("main")
        if isinstance(main, HotelRecommendationGraphNode):
            main.config = self.config

    monkeypatch.setattr(Graph, "register_nodes", patched_register_nodes)

    original_soft_rerank_init = SoftRerankNode.__init__

    def patched_soft_rerank_init(self, llm=None):
        original_soft_rerank_init(self, _PromptLLMAdapter(llm) if llm is not None else None)

    monkeypatch.setattr(SoftRerankNode, "__init__", patched_soft_rerank_init)

    kb = InMemoryPolicyKB()
    kb.build(
        "acme",
        [
            "standard 大阪 per-diem 11000 approved vendor Rakuten cancellation flexible",
            "standard 大阪 approved vendor Jalan breakfast wifi laundry",
        ],
    )
    search = StaticHotelSearch(
        [
            {
                "id": "osaka-001",
                "name": "大阪ビジネスホテル駅前",
                "city": "大阪",
                "vendor": "Rakuten",
                "price": 9800,
                "url": "https://example.invalid/hotel/osaka-001",
                "amenities": ["breakfast", "wifi", "laundry"],
                "distance_km": 0.6,
            },
            {
                "id": "osaka-002",
                "name": "大阪コンファレンスホテル",
                "city": "大阪",
                "vendor": "Jalan",
                "price": 10800,
                "url": "https://example.invalid/hotel/osaka-002",
                "amenities": ["wifi"],
                "distance_km": 1.2,
            },
        ]
    )
    return {"kb": kb, "search": search}


@pytest.fixture
def case_payload() -> str:
    return "大阪 出張 ホテル"


@pytest.fixture
def live_input_context() -> dict:
    return {
        "mode": "recommend",
        "destination": "大阪",
        "per_diem": 11000,
        "tier": "standard",
        "tenant_id": "acme",
    }
