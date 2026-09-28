"""Unit tests — SVC-C2-029 nodes (TC-01..TC-08 coverage)."""

from __future__ import annotations

import inspect

import pytest

from framework.errors import SecurityViolationError
from framework.schemas.agent_status import AgentStatus

from src.nodes.output_assemble_node import OutputAssembleNode
from src.nodes.policy_filter_node import PolicyFilterNode
from src.nodes.policy_nodes import PolicyKBBuildNode, PolicyLoadNode
from src.nodes.post_process_node import PostProcessNode
from src.nodes.pre_process_node import PreProcessNode
from src.nodes.soft_rerank_node import SoftRerankNode
from src.nodes.web_fetch_node import WebFetchNode
from src.schemas.state import State
from src.services.policy_kb import InMemoryPolicyKB
from src.services.web_search import StaticHotelSearch


def _catalog():
    return [
        {"id": "h1", "name": "大阪駅前ホテル", "vendor": "X", "price": 9000,
         "url": "https://ex/h1", "amenities": ["大浴場", "朝食"], "distance_km": 0.3},
        {"id": "h2", "name": "大阪ベイホテル", "vendor": "Y", "price": 10500,
         "url": "https://ex/h2", "amenities": ["wifi"], "distance_km": 3.0},
        {"id": "h3", "name": "大阪高級ホテル", "vendor": "Z", "price": 30000,
         "url": "https://ex/h3", "amenities": ["大浴場"], "distance_km": 1.0},
    ]


# ── PreProcessNode / S-2 ───────────────────────────────────────────────────────

def test_pre_process_extracts_params():
    node = PreProcessNode()
    out = node.execute({"user_input": "大阪 出張",
                        "input_context": {"mode": "recommend", "destination": "大阪", "per_diem": 11000}})
    assert out["status"] == AgentStatus.SUCCESS.value
    assert out["request_params"]["destination"] == "大阪"
    assert out["request_params"]["per_diem"] == 11000


def test_pre_process_empty_input_errors():
    node = PreProcessNode()
    out = node.execute({"user_input": "   "})
    assert out["status"] == AgentStatus.ERROR.value


def test_s2_input_size_limit_raises():
    node = PreProcessNode()
    huge = {"user_input": "x" * 5000}
    with pytest.raises(SecurityViolationError):
        node._extra_security_gate_input(huge)


def test_s2_egress_pii_blocked():
    node = PreProcessNode()
    # PII placed in the SOURCE egress field (input_context) must be rejected.
    # The hook runs before execute(), so it scans input_context (not request_params).
    state = {"user_input": "ok", "input_context": {"destination": "call 090-1234-5678"}}
    with pytest.raises(SecurityViolationError):
        node._extra_security_gate_input(state)


# ── PolicyKB nodes ─────────────────────────────────────────────────────────────

def test_policy_kb_build_and_load():
    kb = InMemoryPolicyKB()
    build = PolicyKBBuildNode(kb=kb)
    out = build.execute({"request_params": {"tenant_id": "acme"},
                         "validated_input": "per-diem standard 11000\napproved vendor X"})
    assert out["status"] == AgentStatus.SUCCESS.value
    assert "built" in out["kb_status"]

    load = PolicyLoadNode(kb=kb)
    lout = load.execute({"request_params": {"tenant_id": "acme", "tier": "standard",
                                            "destination": "大阪", "per_diem": 11000}})
    assert lout["status"] == AgentStatus.SUCCESS.value
    assert lout["policy_constraints"]["per_diem"] == 11000


def test_policy_kb_build_missing_tenant_errors():
    out = PolicyKBBuildNode(kb=InMemoryPolicyKB()).execute(
        {"request_params": {}, "validated_input": "doc"})
    assert out["status"] == AgentStatus.ERROR.value


# ── WebFetchNode + degraded path ───────────────────────────────────────────────

def test_web_fetch_returns_candidates():
    node = WebFetchNode(search=StaticHotelSearch(_catalog()))
    out = node.execute({"request_params": {"destination": "大阪"}})
    assert out["status"] == AgentStatus.SUCCESS.value
    assert len(out["raw_candidates"]) == 3


def test_web_fetch_degrades_on_empty():
    node = WebFetchNode(search=StaticHotelSearch([]))
    out = node.execute({"request_params": {"destination": "どこか"}})
    assert len(out["raw_candidates"]) == 1
    assert out["raw_candidates"][0]["degraded"] is True


def test_web_fetch_degrades_on_exception():
    class Boom:
        def search(self, params):
            raise RuntimeError("upstream down")
        def search_page_url(self, params):
            return "https://fallback"
    out = WebFetchNode(search=Boom()).execute({"request_params": {"destination": "x"}})
    assert out["status"] == AgentStatus.SUCCESS.value  # handled, not raised
    assert out["raw_candidates"][0]["degraded"] is True


# ── PolicyFilterNode (first-pass, deterministic) ───────────────────────────────

def test_policy_filter_drops_clear_over_per_diem():
    node = PolicyFilterNode()
    out = node.execute({"raw_candidates": _catalog(),
                        "policy_constraints": {"per_diem": 11000}})
    ids = {c["id"] for c in out["filtered_candidates"]}
    assert "h3" not in ids  # 30000 >> 11000*1.15 -> dropped
    assert "h1" in ids and "h2" in ids


def test_policy_filter_keeps_unknown_as_needs_review():
    node = PolicyFilterNode()
    out = node.execute({"raw_candidates": [{"id": "d", "degraded": True, "price": None}],
                        "policy_constraints": {"per_diem": 11000}})
    assert out["filtered_candidates"][0]["needs_review"] is True
    assert out["compliance_notes"] == "needs_review"


# ── SoftRerankNode (subset invariant + ordering) ───────────────────────────────

def test_soft_rerank_subset_and_orders_cheaper_nearer_first():
    filt = [dict(c, needs_review=False) for c in _catalog() if c["id"] != "h3"]
    out = SoftRerankNode().execute({"filtered_candidates": filt, "request_params": {"destination": "大阪"}})
    recs = out["ranked_recommendations"]
    assert {r["id"] for r in recs} <= {c["id"] for c in filt}  # never invents
    assert recs[0]["id"] == "h1"  # cheaper + nearer + 大浴場 ranks first
    assert all(r["reason"] for r in recs)


# ── S-3 preservation: PostProcessNode ──────────────────────────────────────────

def test_s3_drops_hallucinated_recommendation():
    node = PostProcessNode()
    state = {
        "filtered_candidates": [{"id": "h1"}],
        "ranked_recommendations": [{"id": "h1", "name": "real"}, {"id": "ghost", "name": "hallucinated"}],
        "result": {"recommendations": [{"name": "real"}, {"name": "hallucinated"}]},
    }
    out = node.execute(state)
    names = {r["name"] for r in out["result"]["recommendations"]}
    assert names == {"real"}  # ghost dropped


def test_s3_all_hallucinated_degrades_to_needs_review():
    node = PostProcessNode()
    state = {
        "filtered_candidates": [{"id": "h1"}],
        "ranked_recommendations": [{"id": "ghost", "name": "x"}],
        "result": {"recommendations": [{"name": "x"}]},
    }
    out = node.execute(state)
    assert out["result"]["compliance_notes"] == "needs_review"
    assert out["result"]["recommendations"] == []


def test_s3_returns_sanitized_ranked_recommendations():
    # Outer state must not keep hallucinated IDs — the sanitized list is returned.
    node = PostProcessNode()
    state = {
        "filtered_candidates": [{"id": "h1"}],
        "ranked_recommendations": [{"id": "h1", "name": "real"}, {"id": "ghost", "name": "x"}],
        "result": {"recommendations": [{"name": "real"}, {"name": "x"}]},
    }
    out = node.execute(state)
    assert {r["id"] for r in out["ranked_recommendations"]} == {"h1"}


def test_s3_pii_in_output_raises():
    node = PostProcessNode()
    with pytest.raises(SecurityViolationError):
        node._extra_security_gate_output({"leak": "contact 090-1234-5678"})


def test_s3_pii_nested_in_recommendations_raises():
    # PII buried in a nested recommendation field must also be caught.
    node = PostProcessNode()
    payload = {"recommendations": [{"name": "h1", "reason": "call 090-1234-5678 for details"}]}
    with pytest.raises(SecurityViolationError):
        node._extra_security_gate_output(payload)


# ── OutputAssembleNode ─────────────────────────────────────────────────────────

def test_output_assemble_adds_disclaimer_and_remaining_budget():
    node = OutputAssembleNode()
    out = node.execute({
        "ranked_recommendations": [{"rank": 1, "name": "h1", "price": 9000, "booking_url": "u", "needs_review": False, "reason": "r"}],
        "request_params": {"destination": "大阪"},
        "policy_constraints": {"per_diem": 11000},
    })
    item = out["result"]["recommendations"][0]
    assert item["remaining_budget_hint"] == 2000
    assert "参考価格" in out["result"]["disclaimer"]


# ── TC-05: node contract (execute signature; no _invoke_impl) ───────────────────

@pytest.mark.parametrize("cls", [PreProcessNode, PostProcessNode, WebFetchNode,
                                 PolicyFilterNode, SoftRerankNode, OutputAssembleNode,
                                 PolicyKBBuildNode, PolicyLoadNode])
def test_node_contract_execute_signature(cls):
    assert hasattr(cls, "execute")
    params = list(inspect.signature(cls.execute).parameters)
    assert params[:2] == ["self", "state"]
    assert "_invoke_impl" not in cls.__dict__


def test_state_is_flat_typeddict():
    # State must not be a Pydantic/dataclass — TypedDict has no __dataclass_fields__
    assert not hasattr(State, "__dataclass_fields__")
