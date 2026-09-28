"""AgentCore Platform v1.0 — SVC-C2-029 State schema."""

# ADR-005: State must be a flat TypedDict (see ADR-005 for the prohibited
# object kinds). LangGraph checkpoints use msgpack serialization; non-primitive model objects
# cause silent corruption. Extend AgentState with agent-specific fields
# only. Do NOT add credentials, secrets, or such model objects.

from __future__ import annotations

from framework.schemas.agent_state import AgentState


class State(AgentState):
    """Business Travel Hotel & Plan Recommendation Agent state.

    Flat TypedDict (primitives + JSON-serializable only). Shared fields
    (user_input, status, session_id, node_history, error_log, etc.) are
    inherited from AgentState.

    Scope legend (docs/02_design.md §State Definition):
      inner = inner HotelRecommendationWorkflow working state (not surfaced)
      outer = elevated to outer state via merge_output() (read by PostProcessNode S-3)
    """

    # ── outer (set by pre_process) ────────────────────────────────────────────
    validated_input: str
    request_params: dict  # mode/destination/check_in/check_out/per_diem/tier/venue/amenities/tenant_id

    # ── inner (working state of the domain workflow) ──────────────────────────
    policy_constraints: dict
    raw_candidates: list
    filter_notes: list

    # ── outer (elevated by merge_output so outer S-3 can cross-check) ──────────
    filtered_candidates: list      # first-pass filter survivors — S-3 ID-crosscheck reference set
    ranked_recommendations: list   # LLM-reranked TOP 3-5 (reason / booking URL / ref price)
    compliance_notes: str          # "compliant" (guideline) / "needs_review" — guideline, not a guarantee
    kb_status: str                 # onboard_policy mode only: KB build result

    # ── outer (final output) ──────────────────────────────────────────────────
    result: dict
