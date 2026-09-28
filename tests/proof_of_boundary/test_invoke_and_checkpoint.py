"""PB-5/PB-6: runtime invoke order, checkpoint safety, S-3 preservation.

Runs the full outer graph and asserts msgpack-safe state and the S-3
ID-crosscheck invariant hold end-to-end (not just at the unit level).
"""

from __future__ import annotations

import json

from framework.schemas.agent_status import AgentStatus

from src.graph.graph import Graph


def _invoke(input_context, user_input="大阪 出張 ホテル", session_id="pb"):
    g = Graph()
    g.compile()
    return g.invoke(user_input, session_id=session_id, input_context=input_context)


def test_recommend_invoke_succeeds_and_backbone_runs():
    res = _invoke({"mode": "recommend", "destination": "大阪", "per_diem": 11000,
                   "tier": "standard", "tenant_id": "acme"})
    assert res.get("status") == AgentStatus.SUCCESS.value or res.get("status") == AgentStatus.SUCCESS
    # fixed 5-node backbone executed
    assert len(res.get("node_history", [])) == 5


def test_output_is_present_and_has_disclaimer():
    res = _invoke({"mode": "recommend", "destination": "大阪", "per_diem": 11000,
                   "tier": "standard", "tenant_id": "acme"})
    output = res.get("output") or {}
    assert "recommendations" in output
    assert "参考価格" in output.get("disclaimer", "")


def test_onboard_policy_mode_builds_kb():
    res = _invoke({"mode": "onboard_policy", "tenant_id": "acme"},
                  user_input="per-diem standard 11000\napproved vendor X")
    output = res.get("output") or {}
    assert output.get("mode") == "onboard_policy"
    assert "kb_status" in output


def test_pb5_final_state_is_serializable():
    # PB-5: checkpointed state must contain only serializable primitives (no
    # Pydantic/dataclass/arbitrary objects). JSON-serializability is a strict
    # proxy for the msgpack constraint for our primitive+dict+list state.
    res = _invoke({"mode": "recommend", "destination": "大阪", "per_diem": 11000,
                   "tier": "standard", "tenant_id": "acme"})
    # AgentStatus enums are framework-controlled; exclude them from the payload check.
    safe = {k: v for k, v in res.items() if not str(type(v)).startswith("<enum")}
    json.dumps(safe, ensure_ascii=False)  # raises on non-serializable objects


def test_pb6_s3_invariant_output_ids_subset_of_filtered():
    # Every emitted recommendation must correspond to a filtered candidate.
    res = _invoke({"mode": "recommend", "destination": "大阪", "per_diem": 11000,
                   "tier": "standard", "tenant_id": "acme"})
    output = res.get("output") or {}
    rec_names = {r.get("name") for r in output.get("recommendations", [])}
    filtered_names = {c.get("name") for c in res.get("filtered_candidates", [])}
    assert rec_names <= filtered_names
