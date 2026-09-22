"""Core ADK agent definition — the brain of the Ford Platform Agent.

Uses Google ADK with:
- Gemini 2.5 Flash for tool routing (Tier 1)
- Claude Sonnet 5 via Vertex AI for complex reasoning (Tier 2, future)
- Dynamic instruction loading from knowledge base
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
from ford_platform_agent.providers.registry import ProviderRegistry
from ford_platform_agent.tools import cicd, gitops, infra, scaffold, scm

SYSTEM_INSTRUCTION = """\
You are Ford's Platform Engineering Agent — an AI assistant that helps developers \
interact with CI/CD pipelines, source control, GitOps deployments, and infrastructure.

## Your Capabilities
- List repositories, check pipeline status, view pull requests
- Trigger CI/CD pipelines (GitHub Actions or Tekton)
- Manage GitOps deployments (ArgoCD: sync, rollback, status)
- Provision infrastructure: generate Terraform configs, create PRs, read plans, merge
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
    scaffold.set_registry(registry)

    init_callbacks(guardrail_config)

    knowledge_context = get_knowledge_instruction(knowledge_dir)
    full_instruction = SYSTEM_INSTRUCTION + knowledge_context

    read_tools = [
        FunctionTool(cicd.list_pipeline_runs),
        FunctionTool(cicd.get_pipeline_status),
        FunctionTool(scm.list_repositories),
        FunctionTool(scm.get_repository_info),
        FunctionTool(scm.list_pull_requests),
        FunctionTool(gitops.list_gitops_applications),
        FunctionTool(gitops.get_application_status),
        FunctionTool(gitops.get_deployment_history),
        FunctionTool(infra.list_environments),
        FunctionTool(infra.get_plan_output),
        FunctionTool(scaffold.list_templates),
    ]

    write_tools = [
        FunctionTool(cicd.trigger_pipeline, require_confirmation=requires_confirmation_for_env),
        FunctionTool(cicd.cancel_pipeline, require_confirmation=True),
        FunctionTool(scm.create_pull_request, require_confirmation=True),
        FunctionTool(gitops.sync_application, require_confirmation=requires_confirmation_for_env),
        FunctionTool(gitops.rollback_application, require_confirmation=True),
        FunctionTool(infra.create_service_pr, require_confirmation=True),
        FunctionTool(infra.approve_and_merge, require_confirmation=True),
        FunctionTool(scaffold.scaffold_project, require_confirmation=True),
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
