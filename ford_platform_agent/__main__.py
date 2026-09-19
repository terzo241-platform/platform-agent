"""CLI entry point for Ford Platform Agent.

Supports two modes:
  1. Interactive: `ford-agent` — starts ADK web UI for chat
  2. Direct: `ford-agent run "list my repos"` — single-shot query via CLI

For production, deploy to Cloud Run: `adk deploy cloud_run`
"""

from __future__ import annotations

import argparse
import asyncio

import structlog

structlog.configure(
    processors=[
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.dev.ConsoleRenderer(),
    ],
    wrapper_class=structlog.make_filtering_bound_logger(20),
)

logger = structlog.get_logger()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="ford-agent",
        description="Ford Platform Engineering Agent — AI-powered platform operations",
    )
    sub = parser.add_subparsers(dest="command")

    run_parser = sub.add_parser("run", help="Execute a single query")
    run_parser.add_argument("query", help="Natural language query to execute")
    run_parser.add_argument("--model", default="", help="Override LLM model")

    sub.add_parser("serve", help="Start the ADK web server")
    sub.add_parser("providers", help="List available providers")

    return parser.parse_args()


async def run_query(query: str, model_override: str = "") -> None:
    """Execute a single query against the agent."""
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai.types import Content, Part

    from ford_platform_agent.agent import build_agent
    from ford_platform_agent.config import AgentConfig

    config = AgentConfig()
    if model_override:
        config = AgentConfig(model=model_override)

    agent, registry = build_agent(agent_config=config)
    session_service = InMemorySessionService()
    runner = Runner(agent=agent, session_service=session_service, app_name=config.app_name)

    session = await session_service.create_session(app_name=config.app_name, user_id="cli-user")

    user_message = Content(role="user", parts=[Part(text=query)])

    try:
        async for event in runner.run_async(
            session_id=session.id, user_id="cli-user", new_message=user_message
        ):
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if hasattr(part, "text") and part.text:
                        print(part.text)
                    elif hasattr(part, "function_call") and part.function_call:
                        logger.debug(
                            "tool_call",
                            function=part.function_call.name,
                            args=part.function_call.args,
                        )
                    elif hasattr(part, "function_response") and part.function_response:
                        logger.debug(
                            "tool_response",
                            function=part.function_response.name,
                        )
    finally:
        await registry.close_all()


def list_providers() -> None:
    """Show which providers are configured."""
    from ford_platform_agent.config import ArgoCDConfig, GitHubConfig, TektonConfig
    from ford_platform_agent.providers.registry import ProviderRegistry

    registry = ProviderRegistry(
        github_config=GitHubConfig(),
        argocd_config=ArgoCDConfig(),
        tekton_config=TektonConfig(),
    )
    available = registry.list_available()

    print("Ford Platform Agent — Available Providers\n")
    for category, providers in available.items():
        status = ", ".join(providers) if providers else "(not configured)"
        print(f"  {category:8s}: {status}")

    print("\nConfigure providers via environment variables (see .env.example)")


def main() -> None:
    args = parse_args()

    if args.command == "run":
        asyncio.run(run_query(args.query, args.model))
    elif args.command == "serve":
        print("Starting ADK web server...")
        print("Run: adk web ford_platform_agent")
        print("Or:  adk api_server ford_platform_agent")
    elif args.command == "providers":
        list_providers()
    else:
        print("Ford Platform Agent v0.1.0")
        print()
        print("Usage:")
        print("  ford-agent run 'list my repos'    — single query")
        print("  ford-agent serve                   — start web server")
        print("  ford-agent providers               — show configured providers")
        print()
        print("Interactive (ADK native):")
        print("  adk web ford_platform_agent        — web chat UI")
        print("  adk run ford_platform_agent        — terminal chat")


if __name__ == "__main__":
    main()
