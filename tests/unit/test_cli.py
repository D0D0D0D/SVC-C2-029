"""Unit tests for the AGENTIC STAR Marketplace entry adapter."""

from __future__ import annotations

import sys
import types
from typing import Any

import pytest

import cli
from framework.schemas.invocation_context import InvocationContext
from framework.schemas.trust_level import TrustLevel
from framework.secrets.base import MissingSecret, NullProvider
from framework.secrets.context import bound_secrets
from src.graph.graph import Graph


@pytest.fixture(autouse=True)
def marketplace_environment(monkeypatch):
    values = {
        "STG_DEFAULT_COMPANY_ID": "company-a",
        "STG_DEFAULT_PROJECT_ID": "project-a",
        "AZURE_OPENAI_API_KEY": "key-a",
        "AZURE_OPENAI_ENDPOINT": "https://azure.example.invalid",
        "AZURE_OPENAI_DEPLOYMENT": "deployment-a",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_marketplace_invoke_discards_runner_context_and_forces_read_mode(monkeypatch):
    captured: dict[str, Any] = {}

    def fake_invoke(self, user_input, session_id="", ctx=None, input_context=None):
        del self
        captured.update(
            user_input=user_input,
            session_id=session_id,
            ctx=ctx,
            input_context=input_context,
        )
        return {"status": "success"}

    monkeypatch.setattr(Graph, "invoke", fake_invoke)
    agent = cli.MarketplaceGraph(config={})
    runner_ctx = InvocationContext(caller_trust_level=TrustLevel.ANONYMOUS, hitl_allowed=True)

    result = agent.invoke(
        '{"mode":"onboard_policy"}',
        session_id="market-session",
        ctx=runner_ctx,
        input_context={
            "conversation_history": ["unscanned free text"],
            "mode": "onboard_policy",
            "tenant_id": "attacker-tenant",
        },
    )

    assert result == {"status": "success"}
    assert captured["input_context"] == {
        "mode": "recommend",
        "tenant_id": "company-a",
        "org_id": "project-a",
    }
    assert captured["ctx"].caller_trust_level is TrustLevel.VERIFIED_EXTERNAL
    assert captured["ctx"].hitl_allowed is False


@pytest.mark.parametrize("env_name", ["STG_DEFAULT_COMPANY_ID", "STG_DEFAULT_PROJECT_ID"])
@pytest.mark.parametrize("invalid", ["", "   ", "bad value", "bad\nvalue"])
def test_scope_rejects_missing_empty_or_whitespace_values(monkeypatch, env_name, invalid):
    monkeypatch.setenv(env_name, invalid)
    with pytest.raises((ValueError, MissingSecret)) as error:
        cli.MarketplaceGraph(config={})
    assert env_name in str(error.value)


@pytest.mark.parametrize("env_name", ["STG_DEFAULT_COMPANY_ID", "STG_DEFAULT_PROJECT_ID"])
def test_scope_rejects_unset_values_at_startup(monkeypatch, env_name):
    monkeypatch.delenv(env_name)
    with pytest.raises(MissingSecret) as error:
        cli.MarketplaceGraph(config={})
    assert env_name in str(error.value)


@pytest.mark.parametrize("env_name", cli._AZURE_OPENAI_SECRET_KEYS)
def test_provision_secrets_rejects_missing_azure_value_at_startup(monkeypatch, env_name):
    monkeypatch.delenv(env_name)
    agent = cli.MarketplaceGraph(config={})
    agent.compile()
    with pytest.raises(MissingSecret) as error:
        agent.provision_secrets(NullProvider())
    assert env_name in str(error.value)


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://example.cognitiveservices.azure.com/openai/responses?api-version=2025-04-01-preview",
        "https://example.services.ai.azure.example/openai",
        "https://example.services.ai.azure.example/v1",
        "https://example.services.ai.azure.example?api-version=2024-10-21",
        "example.services.ai.azure.example",
    ],
    ids=["api-path", "openai-path", "base-path", "query-only", "missing-scheme"],
)
def test_unusable_azure_endpoint_shape_fails_during_startup(monkeypatch, endpoint):
    """AzureOpenAIClient appends its own path, so anything but a bare endpoint breaks.

    The resulting error surfaces inside complete(); a node that swallows it
    degrades silently while the invocation still reports status: success, so the
    adapter has to reject the shape at startup instead.
    """

    agent = cli.MarketplaceGraph(config={})
    agent.compile()
    monkeypatch.setenv("AZURE_OPENAI_API_KEY", "gate-test-key")
    monkeypatch.setenv("AZURE_OPENAI_DEPLOYMENT", "gate-test-deployment")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", endpoint)

    with pytest.raises(ValueError, match="bare"):
        agent.provision_secrets(NullProvider())


def test_allowlisted_provider_hides_undeclared_environment(monkeypatch):
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-leak")
    provider = cli._AllowlistedEnvironmentProvider()
    assert provider.get("UNRELATED_SECRET") is None


def test_azure_llm_creates_fresh_client_and_resolves_secrets_per_call(monkeypatch):
    constructed: list[dict[str, str]] = []

    class RecordingAzureClient:
        def __init__(self, config):
            constructed.append(dict(config))
            self.config = config

        def complete(self, messages):
            assert messages[0]["content"] == "rank these hotels"
            return {"content": self.config["api_key"]}

    fake_module = types.ModuleType("shared.services.llm.azure_openai_client")
    fake_module.AzureOpenAIClient = RecordingAzureClient
    monkeypatch.setitem(sys.modules, fake_module.__name__, fake_module)

    llm = cli._AzureMarketplaceLLM()
    provider = cli._AllowlistedEnvironmentProvider()
    with bound_secrets(provider):
        assert llm.complete("rank these hotels") == "key-a"
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "key-b")
        assert llm.complete("rank these hotels") == "key-b"

    assert [config["api_key"] for config in constructed] == ["key-a", "key-b"]
    assert not hasattr(llm, "_client")
    assert not hasattr(llm, "_cached_client")


def test_compile_wires_credential_free_azure_facade_into_inner_graph():
    agent = cli.MarketplaceGraph(config={})
    agent.compile()
    main = agent._nodes["main"]
    assert main.config["llm"] is agent.config["llm"]
    assert isinstance(main.config["llm"], cli._AzureMarketplaceLLM)
