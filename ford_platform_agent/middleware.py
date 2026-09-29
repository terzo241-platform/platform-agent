"""Shared middleware — wraps ALL tool calls regardless of entry path (ADK or MCP).

Enforces rate limiting, environment protection, and audit logging at the
tool level. Both agent.py (ADK path) and mcp_server.py (MCP path) route
through this layer, ensuring consistent guardrails across every interface.
"""

from __future__ import annotations

import functools
import inspect
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, Coroutine

import structlog

from ford_platform_agent.config import GuardrailConfig

logger = structlog.get_logger()


@dataclass(frozen=True)
class ToolMeta:
    """Metadata about a tool, used by middleware for enforcement decisions."""

    name: str
    is_destructive: bool = False
    is_read_only: bool = True


DESTRUCTIVE_TOOLS: frozenset[str] = frozenset({
    "trigger_pipeline",
    "cancel_pipeline",
    "sync_application",
    "rollback_application",
    "create_pull_request",
    "create_service_pr",
    "approve_and_merge",
    "scaffold_project",
})

PROD_GUARDED_TOOLS: frozenset[str] = frozenset({
    "sync_application",
    "rollback_application",
    "trigger_pipeline",
})


class PerUserRateLimiter:
    """Sliding-window rate limiter keyed by user_id."""

    def __init__(self, max_per_min: int = 30) -> None:
        self._max = max_per_min
        self._buckets: dict[str, list[float]] = {}

    def check(self, user_id: str = "__global__") -> bool:
        now = time.monotonic()
        bucket = self._buckets.setdefault(user_id, [])
        bucket[:] = [t for t in bucket if now - t < 60]
        if len(bucket) >= self._max:
            return False
        bucket.append(now)
        return True


class ToolMiddleware:
    """Shared middleware that wraps tool callables for both ADK and MCP paths.

    Usage::

        middleware = ToolMiddleware(guardrail_config)
        wrapped = middleware.wrap(cicd.trigger_pipeline, ToolMeta(
            name="trigger_pipeline", is_destructive=True, is_read_only=False,
        ))
        # wrapped has the same signature as cicd.trigger_pipeline
    """

    def __init__(self, config: GuardrailConfig | None = None) -> None:
        self._config = config or GuardrailConfig()
        self._rate_limiter = PerUserRateLimiter(self._config.rate_limit_per_min)

    def wrap(
        self,
        fn: Callable[..., Coroutine[Any, Any, dict]],
        meta: ToolMeta,
        user_id: str = "__global__",
    ) -> Callable[..., Coroutine[Any, Any, dict]]:
        """Return a new async callable with the same signature, middleware applied."""

        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> dict:
            if not self._rate_limiter.check(user_id):
                logger.warning("middleware_rate_limit", tool=meta.name, user_id=user_id)
                return {
                    "error": "rate_limit_exceeded",
                    "message": "Rate limit exceeded. Please wait before making more requests.",
                }

            if meta.name in PROD_GUARDED_TOOLS:
                env = _extract_environment(fn, args, kwargs)
                if env and env.lower() in self._config.protected_environments:
                    logger.warning(
                        "middleware_env_blocked",
                        tool=meta.name,
                        environment=env,
                        user_id=user_id,
                    )
                    return {
                        "error": "environment_protected",
                        "message": (
                            f"Tool '{meta.name}' targeting '{env}' requires human approval. "
                            f"Use the chat interface for interactive confirmation."
                        ),
                        "environment": env,
                        "tool": meta.name,
                    }

            start = time.monotonic()
            try:
                result = await fn(*args, **kwargs)
            except Exception as exc:
                duration_ms = int((time.monotonic() - start) * 1000)
                logger.error(
                    "middleware_tool_error",
                    tool=meta.name,
                    error=str(exc),
                    duration_ms=duration_ms,
                    user_id=user_id,
                )
                raise

            duration_ms = int((time.monotonic() - start) * 1000)
            logger.info(
                "middleware_tool_call",
                tool=meta.name,
                is_destructive=meta.is_destructive,
                duration_ms=duration_ms,
                user_id=user_id,
                timestamp=datetime.now(UTC).isoformat(),
                has_error="error" in result if isinstance(result, dict) else False,
            )
            return result

        return wrapper


def _extract_environment(
    fn: Callable,
    args: tuple,
    kwargs: dict,
) -> str:
    """Extract the 'environment' argument from args/kwargs."""
    env = kwargs.get("environment", "")
    if env:
        return env
    sig = inspect.signature(fn)
    params = list(sig.parameters.keys())
    if "environment" in params:
        idx = params.index("environment")
        if idx < len(args):
            return str(args[idx])
    return ""
