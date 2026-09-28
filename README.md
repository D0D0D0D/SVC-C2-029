# SVC-C2-029 — Business Travel Hotel & Plan Recommendation Agent

> **Category**: Cat 2 (domain workflow)
> **Industry**: SVC

## Overview

This agent recommends hotels for a business trip. Given the destination, dates, meeting venue, a
per-diem guideline, the traveller's role tier and any amenity preferences, it loads the
organization's own travel-policy rules, gathers hotel candidates, removes the ones that are
clearly outside policy, ranks the rest, and returns three to five options with booking links,
reference prices, the reason for each, and a policy note.

It suits the moment a traveller has a destination and a date range and needs options that will
actually pass expense review: "Osaka, 15–16 July, meeting near the station, within budget" — and
then "cheaper?", "closer to the station?" as follow-ups.

Two things are worth being precise about, because the name suggests more than the code does:

- **The ordering is deterministic, not a model judgement.** Candidates are scored by a fixed
  weighted heuristic — policy fit (a candidate flagged `needs_review` is pushed down), price,
  distance from the venue, and business-trip amenities — and the top five are returned in that
  order. The language model writes the *rationale text* attached to each recommendation; it does
  not choose or reorder them. That is deliberate: a reproducible ordering is testable, and a model
  that silently reorders policy-relevant results is not.
- **The policy filter is a first pass, not a compliance ruling.** It drops candidates that are
  clearly outside the loaded rules. Anything ambiguous comes back marked `needs_review` and is
  the traveller's call. The agent does not book anything, does not guarantee price or
  availability, and does not certify that an expense will be reimbursed.

This is an agent template built with the **AGENTIC STAR** development platform and the
**AgentCore Framework**. It is intended to be taken as a starting point: fork it, adapt it to
your own data and policies, and run it inside your own AGENTIC STAR deployment.

## Integration

🔴 **The shipped standalone server runs entirely on stubs.** `src/api/server.py` constructs
`Graph()` with **no configuration**, so all three service seams fall back to their local doubles:

| Seam | Shipped default | What a deployment must inject |
|---|---|---|
| `config["search"]` | `StaticHotelSearch()` with an **empty catalogue** — it returns no hotels, only a search-page link | A real hotel-search backend implementing the `HotelSearch` protocol |
| `config["kb"]` | `InMemoryPolicyKB()` — lost on restart | A persistent policy knowledge base implementing the `PolicyKB` protocol |
| `config["llm"]` | `EchoLLMClient()` — returns one canned rationale sentence, no network | Any object with `complete(prompt) -> str` |

The inner workflow resolves each seam in order: an explicit constructor argument (how the tests
inject doubles), then `config["kb" | "search" | "llm"]` threaded from the outer graph, then the
local stub. Until you inject the first two, every recommendation run succeeds and returns a single
`needs_review` entry pointing at a placeholder search URL — a working shape with no real content.

`config/agent.yaml` declares `requires.secrets: []` and `requires.extras: []`; no code path in this
template reads a secret. `config/config.yaml` carries `max_retry`, `timeout_seconds` and
`memory_enabled: true`, but the standalone server passes no configuration at all, so **multi-turn
refinement is not wired on that path** — a caller that wants it constructs `Graph(config=...)`
itself with the checkpointing the framework needs.

Also provide caller authentication in front of the entry adapter.

## Input

`POST /invoke` takes `input` (the request in plain language), an optional `session_id`, and an
`input_context` object that carries the structured parameters:

| Key | Meaning |
|---|---|
| `mode` | `recommend` (default) or `onboard_policy` — the latter builds the policy knowledge base from the policy text passed as `input` |
| `tenant_id` | Which policy partition to read within this deployment's own store |
| `destination`, `check_in`, `check_out` | Where and when |
| `per_diem`, `tier` | Spending guideline and role tier |
| `venue`, `amenities` | Meeting venue (feeds the proximity score) and desired amenities |

`tenant_id` is **not** a cross-tenant capability. Each customer runs an isolated deployment, and
isolation comes from that deployment's own credentials — `tenant_id` only selects a partition
inside the one store it can reach. Do not run this template multi-tenant in a single process on
the strength of that field.

Callers must be authenticated. The entry adapter uses a verified middleware trust level when
present; otherwise, if `INVOKE_AUTH_TOKEN` is set on the server environment, a matching
`Authorization: Bearer` header promotes the caller to `VERIFIED_EXTERNAL`. A caller that is
neither middleware-verified nor token-authenticated is rejected with **401**. Every node requires
`VERIFIED_EXTERNAL`, so there is no anonymous path into the graph. There is no `/resume` route —
this template is not HITL.

Register the travel policy once per tenant:

```bash
curl -X POST http://localhost:8000/invoke \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${INVOKE_AUTH_TOKEN}" \
  -d '{"input": "…travel policy text…", "session_id": "onboard-001",
       "input_context": {"mode": "onboard_policy", "tenant_id": "your-company"}}'
```

Then ask for recommendations:

```bash
curl -X POST http://localhost:8000/invoke \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${INVOKE_AUTH_TOKEN}" \
  -d '{"input": "大阪 7/15-16 予算内で", "session_id": "user-1",
       "input_context": {"mode": "recommend", "destination": "大阪",
         "check_in": "2026-07-15", "check_out": "2026-07-16",
         "per_diem": 11000, "tier": "standard", "tenant_id": "your-company"}}'
```

## Output

The AgentCore envelope is returned with two added keys — `filtered_candidates` and
`compliance_notes` — so the policy filter's decisions are auditable from outside the agent rather
than only visible in state:

```json
{
  "output": {
    "destination": "大阪",
    "recommendations": [
      {"rank": 1, "name": "…", "vendor": "…", "price": 9800,
       "booking_url": "https://…", "needs_review": false, "reason": "…"}
    ]
  },
  "status": "success",
  "filtered_candidates": [],
  "compliance_notes": null,
  "trace_id": "…", "correlation_id": "…",
  "node_history": ["InitializeNode", "PreProcessNode", "HotelRecommendationGraphNode",
                   "PostProcessNode", "FinalizeNode"]
}
```

Values are illustrative; with the shipped stub search backend the result is a single
`needs_review` entry linking to a placeholder search page.

## Security and Limitations

Every node declares `VERIFIED_EXTERNAL` trust, so the framework's trust gate stops an
insufficiently trusted caller before any node body runs — the adapter's 401 is the first of two
independent checks. Credentials and personal data are kept out of the output by design; secrets are
handled only through the platform's secret provider.

Known limits, stated plainly:

- **Prices are indicative.** Confirm price, availability and policy fit on the linked booking page.
  The agent does not reserve anything and does not promise an expense will be approved.
- **The policy filter is a first pass.** `needs_review` means the agent could not decide, not that
  the option is acceptable.
- **The ranking is a heuristic.** Four weighted factors, fixed weights. It reflects no learning and
  no traveller history.
- **Single-tenant per deployment.** See the note on `tenant_id` above.
- **Web content is third-party and changes.** A candidate's details are only as current as the
  search backend that supplied them.

Output is machine-generated and assistive. The traveller, not the agent, decides.

## Release

- Version: 1.0.0
- Category: fixed multi-step domain workflow (Cat 2), outer graph with an inner workflow subgraph
- Generation mode: `llm` (used for the recommendation rationale only)
- HITL: not enabled
- Persistence: `memory_enabled: true` in `config/config.yaml`, but not applied on the standalone path
- Required trust level: `VERIFIED_EXTERNAL`

## Requirements

**This template does not run standalone.** It requires:

| Requirement | Notes |
|---|---|
| **AGENTIC STAR platform** | The agent is built to run on the platform, which supplies the caller authentication, the persistent policy store and the search backend. Deployment guides and API documentation: [AGENTIC STAR Developers](https://developers.fd.agenticstar.tm.softbank.jp/) |
| **AgentCore Framework** (`agenticstar-agentcore`) | Supplied by the platform's own package registry at build time. It is not published on public PyPI, so `pip install -e .` alone does not fetch it. |
| Python | >=3.11 |

```bash
pip install -e .
```

### Behaviour without the platform

The framework is designed to run on AGENTIC STAR, and the agent does not fall back to a degraded
mode when the platform is absent — but it does start, and it does answer. The standalone server
compiles the graph and provisions its secret provider at import; `/health` returns
`{"status": "ok"}`; and with `INVOKE_AUTH_TOKEN` set and a valid bearer token an authenticated
`/invoke` returns `status: success`. What comes back is the stub result described under
*Integration*: one `needs_review` recommendation pointing at a placeholder search URL, with the
canned rationale sentence. Without `INVOKE_AUTH_TOKEN`, every request is **401**. The test suite
runs without a platform connection, against the same doubles.

## Quick Start

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest tests/ -v
```

## Documentation

`docs/02_design.md` carries the node flow, the two-mode routing, the state schema and the
security design. `docs/03_test_spec.md` records the test specification and the verification
results.

## Project Structure

```
src/          agent implementation (nodes, services, schemas)
tests/        unit, integration and boundary tests
config/       agent configuration
docs/         design and test specifications
```

See `docs/02_design.md` for the design and `docs/03_test_spec.md` for the test specification.

## Customising

1. Adjust `config/` for your own environment and policies.
2. Replace the knowledge sources and sample data with your own.
3. Review the node implementations under `src/nodes/` for domain-specific logic.
4. Re-run the test suite.

## License

MIT — see [LICENSE](LICENSE).

## Status of this repository

This template is published **as is**, by its individual author, under the MIT license. It carries
**no warranty and no support commitment**, and no organisation stands behind its behaviour or
fitness for any purpose. Issues and pull requests may or may not receive a response; that is at
the sole discretion of the repository owner.
