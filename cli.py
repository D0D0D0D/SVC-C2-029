"""AGENTIC STAR Marketplace entry adapter for SVC-C2-029."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, cast
from urllib.parse import quote, urlsplit
from urllib.request import Request, urlopen

from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.base import SecretProvider
from framework.secrets.context import current_secrets

try:
    from framework.utils.config_loader import load_agent_config
except ImportError:  # AgentCore 1.0.0 / 1.0.1 compatibility
    from framework.utils.config_loader import load_config

    def load_agent_config(agent_dir: Path) -> dict[str, Any]:
        config_path = agent_dir / "config" / "config.yaml"
        return load_config(str(config_path)) if config_path.exists() else {}


try:
    from shared.bootstrap import run_agent_marketplace
except ModuleNotFoundError as exc:  # AgentCore 1.0.0 / 1.0.1 compatibility
    if exc.name != "shared.bootstrap":
        raise
    _BOOTSTRAP_IMPORT_ERROR: ModuleNotFoundError | None = exc

    def run_agent_marketplace(*args: Any, **kwargs: Any) -> None:
        del args, kwargs
        raise RuntimeError(
            "The installed AgentCore wheel does not provide Marketplace bootstrap support"
        ) from _BOOTSTRAP_IMPORT_ERROR
else:
    _BOOTSTRAP_IMPORT_ERROR = None


try:
    from shared.secrets.env_provider import EnvProvider
except ModuleNotFoundError as exc:  # AgentCore 1.0.0 / 1.0.1 compatibility
    if exc.name != "shared.secrets.env_provider":
        raise

    class EnvProvider(SecretProvider):  # type: ignore[no-redef]
        """Minimal environment provider for AgentCore versions before 1.0.2."""

        def __init__(self, namespace: str = "", agent_name: str = "") -> None:
            self._namespace = namespace
            self._agent_name = agent_name

        def get(self, key: str, default: str | None = None) -> str | None:
            return os.environ.get(key, default)


from src.entry_adapter import (
    STG_DEFAULT_ORG_ENV,
    STG_DEFAULT_TENANT_ENV,
    VERIFIED_ORG_STATE_ATTR,
    VERIFIED_TENANT_STATE_ATTR,
)
from src.graph.graph import Graph, HotelRecommendationGraphNode

_NAMESPACE = "svc"
_AGENT_NAME = "SVC-C2-029"
_PROVIDER_AGENT_NAME = "business_travel_hotel_agent"

_AZURE_OPENAI_SECRET_KEYS = (
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_DEPLOYMENT",
)

# This parts revision exposes the verified state attributes and STG environment
# names separately rather than exporting SCOPE_KEYS/STG_DEFAULT_SCOPE_ENVS.
# Keep the correspondence explicit and validate it before deriving State scope.
SCOPE_KEYS = ("tenant_id", "org_id")
_SCOPE_DECLARATIONS = (
    (SCOPE_KEYS[0], VERIFIED_TENANT_STATE_ATTR, STG_DEFAULT_TENANT_ENV),
    (SCOPE_KEYS[1], VERIFIED_ORG_STATE_ATTR, STG_DEFAULT_ORG_ENV),
)
STG_DEFAULT_SCOPE_ENVS = {state_key: env_name for state_key, _verified_attr, env_name in _SCOPE_DECLARATIONS}
_SCOPE_ENV_KEYS = tuple(STG_DEFAULT_SCOPE_ENVS[key] for key in SCOPE_KEYS)
_REQUIRED_ENV_KEYS = (*_AZURE_OPENAI_SECRET_KEYS, *_SCOPE_ENV_KEYS)
_ALLOWED_ENV_KEYS = frozenset(_REQUIRED_ENV_KEYS)


class _AllowlistedEnvironmentProvider(EnvProvider):  # type: ignore[misc]
    """Expose only this single-agent container's declared environment values."""

    def __init__(self) -> None:
        super().__init__(namespace=_NAMESPACE, agent_name=_PROVIDER_AGENT_NAME)

    def get(self, key: str, default: str | None = None) -> str | None:
        if key not in _ALLOWED_ENV_KEYS:
            return default
        return super().get(key, default)


def _validate_scope_declarations() -> None:
    if set(STG_DEFAULT_SCOPE_ENVS) != set(SCOPE_KEYS):
        raise RuntimeError("Marketplace scope declarations do not match SCOPE_KEYS")
    if len({verified_attr for _, verified_attr, _ in _SCOPE_DECLARATIONS}) != len(SCOPE_KEYS):
        raise RuntimeError("Marketplace verified scope attributes must be unique")


def _require_clean(provider: SecretProvider, key: str) -> str:
    """Resolve a required deployment value without repairing invalid input."""

    value = provider.require(key)
    if not value or any(character.isspace() for character in value):
        raise ValueError(f"Marketplace environment variable {key!r} " "must be a non-empty string without whitespace")
    return value


def _require_azure_endpoint(provider: SecretProvider) -> str:
    """Reject ``AZURE_OPENAI_ENDPOINT`` values that are not a bare Azure resource endpoint.

    ``AzureOpenAIClient`` appends its own ``/openai/v1/`` path, so anything
    beyond a scheme and host silently produces a broken request URL, and the
    failure surfaces inside ``complete()``. A node that swallows that exception
    degrades quietly while the invocation still reports ``status: success``,
    which is why this has to be a startup check. The value is reported here,
    never repaired.
    """

    endpoint = _require_clean(provider, "AZURE_OPENAI_ENDPOINT")
    parts = urlsplit(endpoint)
    try:
        port = parts.port
    except ValueError:
        port = -1
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or port == -1
        or parts.path.strip("/")
        or parts.query
        or parts.fragment
        or any(character.isspace() for character in endpoint)
    ):
        raise ValueError(
            "Marketplace environment variable 'AZURE_OPENAI_ENDPOINT' must be a bare Azure "
            "resource endpoint -- scheme and host only, with no path (such as "
            "'/openai'), query or fragment"
        )
    return endpoint


def _scope_from_environment(provider: SecretProvider) -> dict[str, str]:
    """Build the complete adapter-owned scope from entry-adapter declarations."""

    _validate_scope_declarations()
    scope = {state_key: _require_clean(provider, STG_DEFAULT_SCOPE_ENVS[state_key]) for state_key in SCOPE_KEYS}
    if set(scope) != set(SCOPE_KEYS):
        raise RuntimeError("Marketplace scope is incomplete")
    return scope


class _AzureOpenAIClientCompat:
    """Small Azure chat-completions client for AgentCore 1.0.0/1.0.1.

    AgentCore 1.0.2 supplies ``AzureOpenAIClient``. Older wheels do not, so the
    adapter keeps their runtime usable without adding an OpenAI SDK dependency.
    """

    def __init__(self, config: dict[str, str]) -> None:
        self._config = config

    def complete(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        endpoint = self._config["azure_endpoint"].rstrip("/")
        deployment = quote(self._config["azure_deployment"], safe="")
        url = f"{endpoint}/openai/deployments/{deployment}/chat/completions" "?api-version=2024-10-21"
        body = json.dumps({"messages": messages}).encode("utf-8")
        request = Request(
            url,
            data=body,
            headers={
                "api-key": self._config["api_key"],
                "content-type": "application/json",
            },
            method="POST",
        )
        with urlopen(request, timeout=30) as response:  # noqa: S310 - validated deployment endpoint
            payload = json.loads(response.read().decode("utf-8"))
        choices = payload.get("choices", []) if isinstance(payload, dict) else []
        content = choices[0].get("message", {}).get("content", "") if choices else ""
        return {"content": content, "usage": payload.get("usage", {})}


class _AzureMarketplaceLLM:
    """Resolve Azure credentials and construct a fresh SDK client per call."""

    def complete(self, prompt: str) -> str:
        # Azure support is an optional SDK extra and is the only lazy import.
        # Framework/security imports stay at module load time because the runner
        # invokes this graph through asyncio.to_thread().
        try:
            from shared.services.llm.azure_openai_client import AzureOpenAIClient
        except ModuleNotFoundError as exc:  # AgentCore 1.0.0 / 1.0.1 compatibility
            if exc.name != "shared.services.llm.azure_openai_client":
                raise
            AzureOpenAIClient = _AzureOpenAIClientCompat

        provider = current_secrets()
        client = AzureOpenAIClient(
            {
                "api_key": _require_clean(provider, "AZURE_OPENAI_API_KEY"),
                "azure_endpoint": _require_azure_endpoint(provider),
                "azure_deployment": _require_clean(provider, "AZURE_OPENAI_DEPLOYMENT"),
            }
        )
        response = client.complete([{"role": "user", "content": prompt}])
        if isinstance(response, dict):
            return str(response.get("content", ""))
        return str(response)


class MarketplaceGraph(Graph):
    """Marketplace-only read boundary, deployment scope, and Azure wiring."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        # The runner constructs and compiles before provision_secrets(). Validate
        # deployment-owned scope now so an invalid Pod never starts serving.
        _scope_from_environment(_AllowlistedEnvironmentProvider())
        marketplace_config = dict(config or {})
        marketplace_config["llm"] = _AzureMarketplaceLLM()
        super().__init__(config=marketplace_config)

    def register_nodes(self) -> None:
        super().register_nodes()
        # The existing GraphNode accepts services through its config seam. Wire
        # the credential-free facade before compile; credentials remain per-call.
        main = self._nodes.get("main")
        if isinstance(main, HotelRecommendationGraphNode):
            main.config = self.config

    def invoke(
        self,
        user_input: str,
        session_id: str = "",
        ctx: InvocationContext | None = None,
        input_context: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # Runner conversation_history is free text outside this AG's S-2 scan.
        # Rebuild input_context exclusively from adapter-owned, validated data.
        del input_context
        scope = _scope_from_environment(_AllowlistedEnvironmentProvider())
        marketplace_input_context = {"mode": "recommend", **scope}

        # Marketplace has no policy-onboarding/write channel. Its caller is an
        # authenticated external caller regardless of the runner's ctx default.
        base_ctx = ctx if ctx is not None else InvocationContext(session_id=session_id or "")
        marketplace_ctx = replace(
            base_ctx,
            caller_trust_level=TrustLevel.VERIFIED_EXTERNAL,
            hitl_allowed=False,
        )
        return cast(
            dict[str, Any],
            super().invoke(
                user_input,
                session_id=session_id,
                ctx=marketplace_ctx,
                input_context=marketplace_input_context,
            ),
        )

    def provision_secrets(self, provider: SecretProvider) -> None:
        # Ignore a runner provider that may expose dotenv or other agents' keys.
        # Compile has already completed; validate all required values now.
        del provider
        environment_provider = _AllowlistedEnvironmentProvider()
        for key in _AZURE_OPENAI_SECRET_KEYS:
            _require_clean(environment_provider, key)
        _scope_from_environment(environment_provider)
        _require_azure_endpoint(environment_provider)
        super().provision_secrets(environment_provider)


def main() -> None:
    run_agent_marketplace(
        MarketplaceGraph,
        agent_name=_AGENT_NAME,
        namespace=_NAMESPACE,
        config=load_agent_config(Path(__file__).resolve().parent),
    )


if __name__ == "__main__":
    main()
