"""Tests for the shared middleware layer."""

from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError

import pytest

from ford_platform_agent.config import GuardrailConfig
from ford_platform_agent.middleware import (
    DESTRUCTIVE_TOOLS,
    PROD_GUARDED_TOOLS,
    PerUserRateLimiter,
    ToolMeta,
    ToolMiddleware,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _dummy_tool(repo: str, environment: str = "", ci_provider: str = "auto") -> dict:
    """Fake tool for testing."""
    return {"status": "ok", "repo": repo, "environment": environment}


async def _failing_tool(repo: str) -> dict:
    raise RuntimeError("provider down")


# ---------------------------------------------------------------------------
# ToolMeta
# ---------------------------------------------------------------------------


class TestToolMeta:
    def test_defaults(self):
        meta = ToolMeta(name="foo")
        assert meta.is_read_only is True
        assert meta.is_destructive is False

    def test_frozen(self):
        meta = ToolMeta(name="foo")
        with pytest.raises(FrozenInstanceError):
            meta.name = "bar"  # type: ignore[misc]

    def test_write_tool_meta(self):
        meta = ToolMeta(name="sync_application", is_destructive=True, is_read_only=False)
        assert meta.is_destructive is True
        assert meta.is_read_only is False


# ---------------------------------------------------------------------------
# PerUserRateLimiter
# ---------------------------------------------------------------------------


class TestPerUserRateLimiter:
    def test_allows_under_limit(self):
        limiter = PerUserRateLimiter(max_per_min=5)
        for _ in range(5):
            assert limiter.check("user-a") is True

    def test_blocks_over_limit(self):
        limiter = PerUserRateLimiter(max_per_min=3)
        for _ in range(3):
            limiter.check("user-a")
        assert limiter.check("user-a") is False

    def test_per_user_isolation(self):
        limiter = PerUserRateLimiter(max_per_min=2)
        limiter.check("user-a")
        limiter.check("user-a")
        assert limiter.check("user-a") is False
        assert limiter.check("user-b") is True

    def test_global_default_bucket(self):
        limiter = PerUserRateLimiter(max_per_min=1)
        assert limiter.check() is True
        assert limiter.check() is False


# ---------------------------------------------------------------------------
# ToolMiddleware — rate limiting
# ---------------------------------------------------------------------------


class TestMiddlewareRateLimit:
    @pytest.fixture
    def middleware(self):
        config = GuardrailConfig(rate_limit_per_min=3)
        return ToolMiddleware(config)

    async def test_passes_under_limit(self, middleware):
        meta = ToolMeta(name="list_pipeline_runs")
        wrapped = middleware.wrap(_dummy_tool, meta)
        result = await wrapped("my-repo")
        assert result["status"] == "ok"

    async def test_blocks_over_limit(self, middleware):
        meta = ToolMeta(name="list_pipeline_runs")
        wrapped = middleware.wrap(_dummy_tool, meta)
        for _ in range(3):
            await wrapped("my-repo")
        result = await wrapped("my-repo")
        assert result["error"] == "rate_limit_exceeded"

    async def test_error_structure(self, middleware):
        meta = ToolMeta(name="list_pipeline_runs")
        wrapped = middleware.wrap(_dummy_tool, meta)
        for _ in range(3):
            await wrapped("my-repo")
        result = await wrapped("my-repo")
        assert "message" in result


# ---------------------------------------------------------------------------
# ToolMiddleware — environment protection
# ---------------------------------------------------------------------------


class TestMiddlewareEnvProtection:
    @pytest.fixture
    def middleware(self):
        config = GuardrailConfig(approval_required_envs="prod,production")
        return ToolMiddleware(config)

    async def test_blocks_prod_for_guarded_tools(self, middleware):
        meta = ToolMeta(name="trigger_pipeline", is_destructive=True, is_read_only=False)
        wrapped = middleware.wrap(_dummy_tool, meta)
        result = await wrapped("my-repo", environment="prod")
        assert result["error"] == "environment_protected"

    async def test_allows_dev_for_guarded_tools(self, middleware):
        meta = ToolMeta(name="trigger_pipeline", is_destructive=True, is_read_only=False)
        wrapped = middleware.wrap(_dummy_tool, meta)
        result = await wrapped("my-repo", environment="dev")
        assert result["status"] == "ok"

    async def test_allows_prod_for_read_only(self, middleware):
        meta = ToolMeta(name="list_pipeline_runs")
        wrapped = middleware.wrap(_dummy_tool, meta)
        result = await wrapped("my-repo", environment="prod")
        assert result["status"] == "ok"

    async def test_env_case_insensitive(self, middleware):
        meta = ToolMeta(name="sync_application", is_destructive=True, is_read_only=False)
        wrapped = middleware.wrap(_dummy_tool, meta)
        result = await wrapped("my-repo", environment="PROD")
        assert result["error"] == "environment_protected"

    async def test_env_from_kwargs(self, middleware):
        meta = ToolMeta(name="trigger_pipeline", is_destructive=True, is_read_only=False)
        wrapped = middleware.wrap(_dummy_tool, meta)
        result = await wrapped(repo="my-repo", environment="production")
        assert result["error"] == "environment_protected"


# ---------------------------------------------------------------------------
# ToolMiddleware — audit & error handling
# ---------------------------------------------------------------------------


class TestMiddlewareAudit:
    async def test_preserves_exception(self):
        config = GuardrailConfig()
        mw = ToolMiddleware(config)
        meta = ToolMeta(name="failing_tool")
        wrapped = mw.wrap(_failing_tool, meta)
        with pytest.raises(RuntimeError, match="provider down"):
            await wrapped("my-repo")

    async def test_successful_call_returns_result(self):
        config = GuardrailConfig()
        mw = ToolMiddleware(config)
        meta = ToolMeta(name="list_pipeline_runs")
        wrapped = mw.wrap(_dummy_tool, meta)
        result = await wrapped("my-repo")
        assert result == {"status": "ok", "repo": "my-repo", "environment": ""}


# ---------------------------------------------------------------------------
# ToolMiddleware — wrapping preserves function metadata
# ---------------------------------------------------------------------------


class TestMiddlewareWrapping:
    def test_preserves_name(self):
        mw = ToolMiddleware()
        meta = ToolMeta(name="list_pipeline_runs")
        wrapped = mw.wrap(_dummy_tool, meta)
        assert wrapped.__name__ == "_dummy_tool"

    def test_preserves_docstring(self):
        mw = ToolMiddleware()
        meta = ToolMeta(name="list_pipeline_runs")
        wrapped = mw.wrap(_dummy_tool, meta)
        assert wrapped.__doc__ == "Fake tool for testing."

    def test_preserves_signature(self):
        mw = ToolMiddleware()
        meta = ToolMeta(name="list_pipeline_runs")
        wrapped = mw.wrap(_dummy_tool, meta)
        original_sig = inspect.signature(_dummy_tool)
        wrapped_sig = inspect.signature(wrapped)
        assert str(original_sig) == str(wrapped_sig)

    async def test_passes_args_through(self):
        mw = ToolMiddleware()
        meta = ToolMeta(name="test")
        wrapped = mw.wrap(_dummy_tool, meta)
        result = await wrapped("my-repo", environment="staging", ci_provider="tekton")
        assert result["repo"] == "my-repo"
        assert result["environment"] == "staging"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


class TestConstants:
    def test_all_guarded_are_destructive(self):
        assert PROD_GUARDED_TOOLS.issubset(DESTRUCTIVE_TOOLS)

    def test_expected_destructive_count(self):
        assert len(DESTRUCTIVE_TOOLS) == 8

    def test_expected_guarded_count(self):
        assert len(PROD_GUARDED_TOOLS) == 3
