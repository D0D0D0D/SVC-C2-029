"""AgentCore Platform v1.0 — PreProcessNode (SVC-C2-029).

Outer pre_process slot. Validates input and extracts normalized request_params
from the NL / structured request. S-2 domain hook (_extra_security_gate_input)
enforces an input size limit and guarantees no PII is placed into the egress
fields (request_params) that reach the external web search.

Node contract:
 - Extend FunctionNode; implement execute(state) -> dict
 - Return ONLY the fields this node changes
 - Return AgentStatus enum constants
 - Do NOT override _security_gate_input (@final default); extend via
   _extra_security_gate_input only.
"""

from __future__ import annotations

from typing import ClassVar

from framework.errors import SecurityViolationError
from framework.nodes.function_node import FunctionNode
from framework.schemas.trust_level import TrustLevel
from framework.schemas.agent_state import AgentState
from framework.schemas.agent_status import AgentStatus
from shared.security.pii_detector import detect_pii
from shared.utils.audit_logger import emit_trace_event

_MAX_INPUT_CHARS = 4000
# request_params keys that are sent to the external web search (egress).
_EGRESS_KEYS = ("destination", "check_in", "check_out", "venue", "amenities")


class PreProcessNode(FunctionNode):
    """Validate input and normalize request_params (S-2 input gate)."""

    # S-1 trust gate: declared explicitly by design, never inherited implicitly.
    required_trust_level: ClassVar[TrustLevel] = TrustLevel.ANONYMOUS

    def execute(self, state: dict) -> dict:
        user_input = state.get("user_input", "") or ""
        # S-4: at least one domain event per node — the framework audit contract
        emit_trace_event("pre_process_validate", {"input_chars": len(user_input)}, state)
        if not user_input.strip():
            return {
                "status": AgentStatus.ERROR.value,
                "error_log": ["PreProcessNode: user_input is empty or missing"],
            }

        params = self._extract_params(state)
        return {
            "validated_input": user_input.strip(),
            "request_params": params,
            "status": AgentStatus.SUCCESS.value,
        }

    # ── S-2 domain hook (runs BEFORE execute(), after the framework PII scan) ───
    def _extra_security_gate_input(self, state: AgentState) -> AgentState:
        user_input = state.get("user_input", "") or ""
        if len(user_input) > _MAX_INPUT_CHARS:
            raise SecurityViolationError(
                f"PreProcessNode: input exceeds {_MAX_INPUT_CHARS} chars"
            )
        # Egress guard: scan the SOURCE of the egress fields. This hook runs before
        # execute() builds request_params, so we scan input_context (the caller-
        # supplied dict that execute() copies the egress fields from). This is what
        # actually reaches WebFetchNode.
        source = state.get("input_context", {}) or {}
        for key in _EGRESS_KEYS:
            val = source.get(key)
            if isinstance(val, str) and detect_pii(val):
                raise SecurityViolationError(
                    f"PreProcessNode: PII detected in egress field input_context['{key}']"
                )
        return state

    # ── helpers ────────────────────────────────────────────────────────────────
    @staticmethod
    def _extract_params(state: dict) -> dict:
        """Merge structured input_context params with sane defaults.

        NL free-text parsing (Open Design Question) is layered on top of this in
        production; here structured params provided by the caller take priority so
        the pipeline is deterministic and testable.
        """
        provided = state.get("input_context", {}) or {}
        params = {
            "mode": provided.get("mode", "recommend"),
            "destination": provided.get("destination", ""),
            "check_in": provided.get("check_in", ""),
            "check_out": provided.get("check_out", ""),
            "per_diem": provided.get("per_diem"),
            "tier": provided.get("tier", "standard"),
            "venue": provided.get("venue"),
            "amenities": provided.get("amenities", []),
            "tenant_id": provided.get("tenant_id", ""),
        }
        return params
