"""AgentCore Platform v1.0 — LLM client injection seam (SVC-C2-029).

framework.llm is not shipped in the wheel, so the LLM is a local injection
seam. SoftRerankNode depends on the ``LLMClient`` protocol; tests inject a
deterministic double (no network, reproducible ordering).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class LLMClient(Protocol):
    """Minimal LLM seam interface."""

    def complete(self, prompt: str) -> str:
        """Return the LLM completion for a prompt (single-shot)."""
        ...


class EchoLLMClient:
    """Deterministic no-network LLM double for tests / local runs.

    Not a real model. Returns a canned rationale string. The rerank ORDER in
    SoftRerankNode is produced by a deterministic heuristic so tests do not
    depend on real model output; this double only supplies rationale text.
    """

    def complete(self, prompt: str) -> str:
        return "Recommended based on policy fit, effective cost, venue proximity and business-trip amenities."
