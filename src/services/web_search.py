"""AgentCore Platform v1.0 — Web hotel search injection seam (SVC-C2-029).

Cross-site hotel search seam. We fetch via generic web search (not a single-OTA
API) so inventory is not biased to one platform. Nodes depend on the
``HotelSearch`` protocol; tests inject a deterministic double.

Backend selection (which web-search tool / provider) is Open Design Question #1
(docs/02_design.md). When a real fetch is unavailable, the node degrades to a
search-page URL candidate — see WebFetchNode.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class HotelSearch(Protocol):
    """Minimal cross-site hotel search seam interface."""

    def search(self, params: dict) -> list[dict]:
        """Return raw hotel candidates for the given normalized request_params.

        Each candidate: ``{"id": str, "name": str, "vendor": str,
        "price": int, "url": str, "amenities": list[str], "distance_km": float}``.
        Only non-PII search parameters are passed in (S-2 egress rule).
        Implementations MUST NOT raise on empty results — return ``[]`` so the
        node can degrade gracefully.
        """
        ...

    def search_page_url(self, params: dict) -> str:
        """Return a search-results page URL preset with property x dates.

        Used for graceful degradation when structured results are unavailable.
        """
        ...


class StaticHotelSearch:
    """Deterministic in-memory HotelSearch for tests / local runs.

    Returns a fixed candidate set filtered by destination. NOT for production.
    """

    def __init__(self, catalog: list[dict] | None = None) -> None:
        self._catalog = catalog or []

    def search(self, params: dict) -> list[dict]:
        dest = str(params.get("destination", "")).strip()
        if not dest:
            return list(self._catalog)
        return [c for c in self._catalog if dest in c.get("name", "") or dest == c.get("city", "")]

    def search_page_url(self, params: dict) -> str:
        dest = str(params.get("destination", ""))
        ci = str(params.get("check_in", ""))
        co = str(params.get("check_out", ""))
        return f"https://example-hotel-search.invalid/search?dest={dest}&in={ci}&out={co}"
