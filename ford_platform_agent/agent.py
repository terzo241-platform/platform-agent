"""Core ADK agent definition — the brain of the Ford Platform Agent.

Uses Google ADK with:
- Gemini 2.5 Flash for tool routing (Tier 1)
- Claude Sonnet 5 via Vertex AI for complex reasoning (Tier 2, future)
- Dynamic instruction loading from knowledge base
- Shared middleware for rate limiting, env protection, audit (all paths)
- Guardrail callbacks (OWASP LLM06 mitigation)
- require_confirmation on destructive operations
"""

from __future__ import annotations

from google.adk.agents import Agent
from google.adk.tools import FunctionTool

from ford_platform_agent.callbacks import (
    after_agent_audit,
    before_agent_guardrail,
    init_callbacks,
    requires_confirmation_for_env,
)
from ford_platform_agent.config import (
    AgentConfig,
    ArgoCDConfig,
    GitHubConfig,
    GuardrailConfig,
    TektonConfig,
)
from ford_platform_agent.knowledge import get_knowledge_instruction
from ford_platform_agent.middleware import ToolMeta, ToolMiddleware
from ford_platform_agent.providers.registry import ProviderRegistry
from ford_platform_agent.tools import cicd, gitops, infra, metrics, scaffold, scm

SYSTEM_INSTRUCTION = """\
You are Ford's Platform Engineering Agent — an AI assistant that helps developers \
interact with CI/CD pipelines, source control, GitOps deployments, and infrastructure.

## Your Capabilities
- List repositories, check pipeline status, view pull requests
- Trigger CI/CD pipelines (GitHub Actions or Tekton)
- Manage GitOps deployments (ArgoCD: sync, rollback, status)
- Provision infrastructure: generate Terraform configs, create PRs, read plans, merge
- Measure engineering effectiveness: DORA metrics, team rollups, trends, recommendations
- Route to the correct provider automatically based on repo configuration

## Operating Principles
1. SAFETY FIRST: For production environments, always require human confirmation.
2. READ BEFORE WRITE: Prefer showing status before taking action. If asked to \
deploy, first show the current state, then confirm the action.
3. PROVIDER AGNOSTIC: Never assume which CI/CD system is running. Use the tools \
to detect. If a user says "deploy", you figure out whether it's GHA, Tekton, or ArgoCD.
4. MINIMAL BLAST RADIUS: Prefer the smallest possible action. Don't sync all apps \
when one was requested.
5. AUDIT EVERYTHING: Every action you take is logged. Be specific about what you did and why.

## Environment Rules
- dev/development: Auto-approve all actions
- staging/stg: Auto-approve, but warn about shared environment
- prod/production: ALWAYS require human confirmation before write operations
- Never set prune=True on ArgoCD sync unless explicitly asked and confirmed

## Response Style
- Be concise. Status checks: 2-3 sentences max.
- Use tables for listing multiple items.
- Include URLs when available so developers can click through.
- If something fails, explain why and suggest the fix.
"""


def build_agent(
    agent_config: AgentConfig | None = None,
    github_config: GitHubConfig | None = None,
    argocd_config: ArgoCDConfig | None = None,
    tekton_config: TektonConfig | None = None,
    guardrail_config: GuardrailConfig | None = None,
    knowledge_dir: str = "knowledge",
) -> tuple[Agent, ProviderRegistry]:
    """Build and return the configured ADK agent and its provider registry."""
    agent_config = agent_config or AgentConfig()
    guardrail_config = guardrail_config or GuardrailConfig()

    registry = ProviderRegistry(
        github_config=github_config,
        argocd_config=argocd_config,
        tekton_config=tekton_config,
    )

    cicd.set_registry(registry)
    scm.set_registry(registry)
    gitops.set_registry(registry)
    infra.set_registry(registry)
    metrics.set_registry(registry)
    scaffold.set_registry(registry)

    init_callbacks(guardrail_config)

    knowledge_context = get_knowledge_instruction(knowledge_dir)
    full_instruction = SYSTEM_INSTRUCTION + knowledge_context

    mw = ToolMiddleware(guardrail_config)
    _M = ToolMeta
    _w = mw.wrap

    read_tools = [
        FunctionTool(_w(cicd.list_pipeline_runs, _M(name="list_pipeline_runs"))),
        FunctionTool(_w(cicd.get_pipeline_status, _M(name="get_pipeline_status"))),
        FunctionTool(_w(scm.list_repositories, _M(name="list_repositories"))),
        FunctionTool(_w(scm.get_repository_info, _M(name="get_repository_info"))),
        FunctionTool(_w(scm.list_pull_requests, _M(name="list_pull_requests"))),
        FunctionTool(_w(gitops.list_gitops_applications, _M(name="list_gitops_applications"))),
        FunctionTool(_w(gitops.get_application_status, _M(name="get_application_status"))),
        FunctionTool(_w(gitops.get_deployment_history, _M(name="get_deployment_history"))),
        FunctionTool(_w(infra.list_environments, _M(name="list_environments"))),
        FunctionTool(_w(infra.get_plan_output, _M(name="get_plan_output"))),
        FunctionTool(_w(scaffold.list_templates, _M(name="list_templates"))),
        FunctionTool(_w(metrics.get_dora_metrics, _M(name="get_dora_metrics"))),
        FunctionTool(_w(metrics.get_team_metrics, _M(name="get_team_metrics"))),
        FunctionTool(_w(metrics.compare_repos, _M(name="compare_repos"))),
        FunctionTool(_w(metrics.get_metric_trends, _M(name="get_metric_trends"))),
        FunctionTool(_w(metrics.get_dora_recommendations, _M(name="get_dora_recommendations"))),
    ]

    write_tools = [
        FunctionTool(
            _w(cicd.trigger_pipeline, _M(name="trigger_pipeline", is_destructive=True, is_read_only=False)),
            require_confirmation=requires_confirmation_for_env,
        ),
        FunctionTool(
            _w(cicd.cancel_pipeline, _M(name="cancel_pipeline", is_destructive=True, is_read_only=False)),
            require_confirmation=True,
        ),
        FunctionTool(
            _w(scm.create_pull_request, _M(name="create_pull_request", is_read_only=False)),
            require_confirmation=True,
        ),
        FunctionTool(
            _w(gitops.sync_application, _M(name="sync_application", is_destructive=True, is_read_only=False)),
            require_confirmation=requires_confirmation_for_env,
        ),
        FunctionTool(
            _w(gitops.rollback_application, _M(name="rollback_application", is_destructive=True, is_read_only=False)),
            require_confirmation=True,
        ),
        FunctionTool(
            _w(infra.create_service_pr, _M(name="create_service_pr", is_read_only=False)),
            require_confirmation=True,
        ),
        FunctionTool(
            _w(infra.approve_and_merge, _M(name="approve_and_merge", is_destructive=True, is_read_only=False)),
            require_confirmation=True,
        ),
        FunctionTool(
            _w(scaffold.scaffold_project, _M(name="scaffold_project", is_read_only=False)),
            require_confirmation=True,
        ),
    ]

    agent = Agent(
        name="ford_platform_agent",
        model=agent_config.model,
        instruction=full_instruction,
        tools=read_tools + write_tools,
        before_agent_callback=before_agent_guardrail,
        after_agent_callback=after_agent_audit,
    )

    return agent, registry


# ADK CLI discovery — `adk run` / `adk web` look for this variable
root_agent, _registry = build_agent()
