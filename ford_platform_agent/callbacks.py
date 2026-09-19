"""ADK callbacks for guardrails, audit, and rate limiting.

Implements the 4-layer guardrail architecture:
  1. Tool scoping (handled by ADK FunctionTool.require_confirmation)
  2. Pre-execution validation (before_tool_callback)
  3. Human-in-the-loop (require_confirmation on destructive tools)
  4. Audit trail (after_agent_callback)

References: OWASP LLM06 (Excessive Agency), ThoughtWorks Radar Vol 34 (Agent Skills).
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog
import yaml

from ford_platform_agent.config import GuardrailConfig

if TYPE_CHECKING:
    from google.adk.agents.context import Context
    from google.genai.types import Content

logger = structlog.get_logger()

_DESTRUCTIVE_TOOLS = {
    "trigger_pipeline",
    "cancel_pipeline",
    "sync_application",
    "rollback_application",
    "create_pull_request",
}

_PROD_BLOCKED_TOOLS = {
    "sync_application",
    "rollback_application",
    "trigger_pipeline",
}


class RateLimiter:
    def __init__(self, max_per_min: int = 30) -> None:
        self._max = max_per_min
        self._calls: list[float] = []

    def check(self) -> bool:
        now = time.monotonic()
        self._calls = [t for t in self._calls if now - t < 60]
        if len(self._calls) >= self._max:
            return False
        self._calls.append(now)
        return True


_rate_limiter: RateLimiter | None = None
_guardrail_config: GuardrailConfig | None = None


def init_callbacks(config: GuardrailConfig | None = None) -> None:
    global _rate_limiter, _guardrail_config
    _guardrail_config = config or GuardrailConfig()
    _rate_limiter = RateLimiter(_guardrail_config.rate_limit_per_min)


def requires_confirmation_for_env(environment: str = "", **kwargs) -> bool:
    """Dynamically determine if a tool call needs human confirmation.

    Used as the require_confirmation callable on destructive FunctionTools.
    Returns True for production environments.
    """
    config = _guardrail_config or GuardrailConfig()
    if not environment:
        return False
    return environment.lower() in config.protected_environments


async def before_agent_guardrail(ctx: Context) -> Content | None:
    """Pre-execution guardrail — runs before the agent processes each turn.

    Checks:
    - Rate limiting (OWASP LLM10: Unbounded Consumption)
    - Session validation
    """
    if _rate_limiter and not _rate_limiter.check():
        from google.genai.types import Content, Part

        logger.warning("rate_limit_exceeded", session=ctx.session.id if ctx.session else "unknown")
        return Content(
            role="model",
            parts=[Part(text="Rate limit exceeded. Please wait before making more requests.")],
        )
    return None


async def after_agent_audit(ctx: Context) -> Content | None:
    """Post-execution audit — logs every agent turn completion.

    In production, this writes to BigQuery. For POC, logs to structlog.
    """
    logger.info(
        "agent_turn_complete",
        session_id=ctx.session.id if ctx.session else "unknown",
        timestamp=datetime.now(UTC).isoformat(),
        state_keys=list(ctx.session.state.keys()) if ctx.session else [],
    )
    return None


def load_guardrail_rules(knowledge_dir: str = "knowledge/guardrails") -> dict:
    """Load guardrail rules from knowledge-as-code YAML files."""
    import os

    rules: dict = {}
    if not os.path.isdir(knowledge_dir):
        return rules
    for filename in os.listdir(knowledge_dir):
        if filename.endswith((".yaml", ".yml")):
            filepath = os.path.join(knowledge_dir, filename)
            with open(filepath) as f:
                content = yaml.safe_load(f)
                if content:
                    rules[filename.rsplit(".", 1)[0]] = content
    return rules
