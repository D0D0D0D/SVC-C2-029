"""AgentCore Platform v1.0 — PostProcessNode (SVC-C2-029).

Outer post_process slot. Formats the final output and applies the S-3 domain
hook (preservation variant):

  - INVARIANT: every emitted recommendation ID must exist in filtered_candidates
    (the first-pass filter survivors elevated to outer state via merge_output).
  - Enforcement is by ID cross-check, not trust: an LLM-hallucinated hotel (ID
    not in filtered_candidates) is DROPPED and audit-logged — sanitize and
    continue (consistent with the degraded-path philosophy). If dropping empties
    the list, degrade to needs_review.
  - PII in the output is a hard stop: raise SecurityViolationError.
  - The framework @final credential scan still runs automatically; this hook
    only adds the domain checks above.

Do NOT override _security_gate_output (default) — extend via
_extra_security_gate_output only.
"""

from __future__ import annotations

from typing import ClassVar

from framework.errors import SecurityViolationError
from framework.nodes.function_node import FunctionNode
from framework.schemas.trust_level import TrustLevel
from framework.schemas.agent_status import AgentStatus
from shared.security.pii_detector import detect_pii
from shared.utils.audit_logger import emit_trace_event


def _iter_strings(obj, path="result"):
    """Yield (path, str) for every string value nested in dicts/lists."""
    if isinstance(obj, str):
        yield path, obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield from _iter_strings(v, f"{path}.{k}")
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            yield from _iter_strings(v, f"{path}[{i}]")


class PostProcessNode(FunctionNode):
    """Format output and enforce S-3 preservation (ID cross-check + PII)."""

    # S-1 trust gate: declared explicitly by design, never inherited implicitly.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict) -> dict:
        # onboard_policy mode: no recommendations — surface the KB build result.
        kb_status = state.get("kb_status")
        if kb_status and not state.get("ranked_recommendations"):
            result = {"mode": "onboard_policy", "kb_status": kb_status,
                      "message": "出張規程 KB を構築しました。以降 recommend モードで利用できます。"}
            return {"result": result, "formatted_output": result, "status": AgentStatus.SUCCESS.value}

        result = state.get("result", {}) or {}
        recs = state.get("ranked_recommendations", []) or []
        allowed_ids = {c.get("id") for c in (state.get("filtered_candidates", []) or [])}

        # ID cross-check: drop hallucinated recommendations (id not in filtered set).
        # onboard_policy mode has no recommendations -> nothing to cross-check.
        # IMPORTANT: return the sanitized ranked_recommendations too, so the outer
        # state (surfaced by merge_output) never keeps hallucinated IDs.
        delta: dict = {"status": AgentStatus.SUCCESS.value}
        if recs:
            kept = [r for r in recs if r.get("id") in allowed_ids]
            dropped = len(recs) - len(kept)
            if dropped:
                emit_trace_event("s3_hallucination_dropped", {"dropped": dropped}, state)
                kept_names = {r.get("name") for r in kept}
                result = dict(result)
                result["recommendations"] = [
                    item for item in result.get("recommendations", [])
                    if item.get("name") in kept_names
                ]
                if not kept:
                    result["compliance_notes"] = "needs_review"
                    result["recommendations"] = []
            delta["ranked_recommendations"] = kept  # sanitized list -> outer state

        delta["result"] = result
        delta["formatted_output"] = result
        return delta

    # ── S-3 domain hook (runs after the framework credential scan) ─────────────
    def _extra_security_gate_output(self, result: dict) -> dict:
        # PII in emitted output is a hard stop (unlike hallucination = drop+continue).
        # Recurse into nested dict/list values so PII in recommendations[].reason,
        # booking_url, name, etc. is also caught — not just top-level strings.
        for path, text in _iter_strings(result):
            if detect_pii(text):
                raise SecurityViolationError(
                    f"PostProcessNode S-3: PII detected in output at {path}"
                )
        return result
