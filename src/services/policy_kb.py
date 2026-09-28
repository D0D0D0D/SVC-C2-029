"""AgentCore Platform v1.0 — Policy KB injection seam (SVC-C2-029).

Travel-policy knowledge base seam. Nodes depend on the ``PolicyKB`` protocol,
never on a concrete vector-DB backend, so tests can inject an in-memory double.
Backend selection is Open Design Question #2 (docs/02_design.md).

Policy interpretation stays deterministic: this seam only stores/retrieves
clause text. Numeric policy judgment (per-diem comparison, approved-vendor
membership) is done in PolicyFilterNode, never by an LLM.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class PolicyKB(Protocol):
    """Minimal policy-KB seam interface (M-02)."""

    def build(self, tenant_id: str, docs: list[str]) -> dict:
        """Chunk, embed and store the tenant's travel-policy documents.

        Returns a kb_status dict, e.g. ``{"chunks": 42, "status": "ok"}``.
        Idempotent per tenant (rebuild overwrites).
        """
        ...

    def retrieve(self, tenant_id: str, query: str, top_k: int = 8) -> list[dict]:
        """Retrieve the most relevant policy clauses for a query.

        Returns ``[{"clause_id": str, "text": str, "score": float}, ...]``.
        """
        ...


class InMemoryPolicyKB:
    """Deterministic in-memory PolicyKB for tests and local runs.

    NOT for production (no persistence, naive substring scoring). Production
    backends live behind the same protocol; select via config/DI.
    """

    def __init__(self) -> None:
        self._store: dict[str, list[str]] = {}

    def build(self, tenant_id: str, docs: list[str]) -> dict:
        chunks: list[str] = []
        for doc in docs:
            chunks.extend(p.strip() for p in doc.split("\n") if p.strip())
        self._store[tenant_id] = chunks
        return {"chunks": len(chunks), "status": "ok"}

    def retrieve(self, tenant_id: str, query: str, top_k: int = 8) -> list[dict]:
        chunks = self._store.get(tenant_id, [])
        terms = [t for t in query.lower().split() if t]
        scored: list[dict] = []
        for i, text in enumerate(chunks):
            low = text.lower()
            score = sum(low.count(t) for t in terms)
            if score > 0:
                scored.append({"clause_id": f"{tenant_id}-{i}", "text": text, "score": float(score)})
        scored.sort(key=lambda c: c["score"], reverse=True)
        return scored[:top_k]
