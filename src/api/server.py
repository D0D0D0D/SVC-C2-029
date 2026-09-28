"""AgentCore Platform v1.0"""

# Standalone HTTP entry point for the agent.
# Entry points are adapters only — no business logic here.
# For platform-level routing, AgentGateway calls agent.invoke() directly.

from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.context import bound_secrets
from shared.secrets import factory as secrets_factory

from src.entry_adapter.auth import _require_verified_trust, _resolve_trust
from src.graph.graph import Graph

app = FastAPI(title="Agent")

agent = Graph()
agent.compile()
# Replace namespace/agent_name to match the agent's manifest values.
agent.provision_secrets(secrets_factory(namespace="svc", agent_name="business_travel_hotel_agent"))


class InvokeRequest(BaseModel):
    input: str
    session_id: str = ""
    # mode/destination/dates/per_diem/tier/venue/amenities/tenant_id (see docs/07)
    input_context: dict = {}


def _authenticated_ctx(request: Request, session_id: str) -> InvocationContext:
    """Build the InvocationContext AFTER entry authentication.

    Entry authentication is handled by entry_adapter.auth:
    verified middleware trust is used as-is; INVOKE_AUTH_TOKEN Bearer auth
    promotes ANONYMOUS → VERIFIED_EXTERNAL (401 on mismatch). An unauthenticated
    caller (still ANONYMOUS, no token promotion) → 401 — replacing the old
    fallback that defaulted such callers to ANONYMOUS. Non-HITL: no /resume route.

    Tenant model (deliberate — single-tenant-per-deployment): `tenant_id` in
    input_context is NOT a cross-tenant capability. Each customer runs an
    ISOLATED deployment; tenant isolation is provided by that deployment's own
    DB/KB credentials (a deployment can only reach its own policy store), NOT by
    per-request scope. `tenant_id` merely selects a KB partition WITHIN the one
    deployment's store, so there is no cross-tenant blast radius to gate at the
    request boundary. (If this template were ever run multi-tenant in a single
    process, the framework would need a scope-carrying InvocationContext to bind
    a verified tenant at the graph boundary — it currently has none; that is a
    framework gap, not a per-request check to bolt onto this adapter.)
    """
    trust, token_authenticated = _resolve_trust(request)
    if trust is TrustLevel.ANONYMOUS and not token_authenticated:
        raise HTTPException(status_code=401, detail="Authentication is required.")
    _require_verified_trust(trust)
    return InvocationContext(
        session_id=session_id or str(uuid4()),
        caller_trust_level=trust,
        caller_id=getattr(request.state, "caller_id", ""),
    )


@app.post("/invoke")
async def invoke(req: InvokeRequest, request: Request):
    ctx = _authenticated_ctx(request, req.session_id)
    with bound_secrets(agent._secrets_provider):
        return agent.invoke(req.input, ctx=ctx, input_context=req.input_context)


@app.get("/health")
def health():
    return {"status": "ok", "agent": "business_travel_hotel_agent"}
