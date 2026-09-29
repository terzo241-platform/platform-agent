"""MCP Server — exposes all Ford Platform Agent tools via Model Context Protocol.

Any MCP-compatible client (Claude Code, VS Code Copilot, Cursor, etc.) can
discover and invoke these tools over stdio or HTTP transport.

All tool calls are routed through the shared ToolMiddleware for rate limiting,
environment protection, and audit logging — same guardrails as the ADK path.

Knowledge files are exposed as MCP resources and prompts so any client LLM
gets the same tribal knowledge the ADK agent gets injected automatically.

Usage:
  ford-agent mcp                          # stdio (for Claude Code / IDE)
  ford-agent mcp --transport streamable-http --port 8080  # HTTP
  python -m ford_platform_agent.mcp_server               # direct
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from ford_platform_agent.config import ArgoCDConfig, GitHubConfig, GuardrailConfig, TektonConfig
from ford_platform_agent.knowledge import KNOWLEDGE_DIR, list_knowledge_files, read_knowledge_file
from ford_platform_agent.middleware import ToolMeta, ToolMiddleware
from ford_platform_agent.providers.registry import ProviderRegistry
from ford_platform_agent.tools import cicd, gitops, infra, metrics, scaffold, scm

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@asynccontextmanager
async def _lifespan(server: MCPServer) -> AsyncIterator[dict]:
    registry = ProviderRegistry(
        github_config=GitHubConfig(),
        argocd_config=ArgoCDConfig(),
        tekton_config=TektonConfig(),
    )
    cicd.set_registry(registry)
    scm.set_registry(registry)
    gitops.set_registry(registry)
    infra.set_registry(registry)
    metrics.set_registry(registry)
    scaffold.set_registry(registry)
    try:
        yield {"registry": registry}
    finally:
        await registry.close_all()


mcp = MCPServer(
    name="ford-platform-agent",
    title="Ford Platform Agent",
    description=(
        "AI-powered platform engineering tools for CI/CD, source control, "
        "GitOps deployments, and infrastructure provisioning. "
        "Includes Ford knowledge base as resources and runbooks as prompts."
    ),
    version="0.2.0",
    lifespan=_lifespan,
)

# ---------------------------------------------------------------------------
# Shared middleware — all tool calls go through this
# ---------------------------------------------------------------------------

_middleware = ToolMiddleware(GuardrailConfig())

# ---------------------------------------------------------------------------
# CI/CD Tools
# ---------------------------------------------------------------------------

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
WRITE_SAFE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)
WRITE_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)

# Pre-wrapped tool functions — created once at import time
_R = ToolMeta  # alias for brevity
_w = _middleware.wrap

_list_pipeline_runs = _w(cicd.list_pipeline_runs, _R(name="list_pipeline_runs"))
_get_pipeline_status = _w(cicd.get_pipeline_status, _R(name="get_pipeline_status"))
_trigger_pipeline = _w(
    cicd.trigger_pipeline,
    _R(name="trigger_pipeline", is_destructive=True, is_read_only=False),
)
_cancel_pipeline = _w(
    cicd.cancel_pipeline,
    _R(name="cancel_pipeline", is_destructive=True, is_read_only=False),
)
_list_repositories = _w(scm.list_repositories, _R(name="list_repositories"))
_get_repository_info = _w(scm.get_repository_info, _R(name="get_repository_info"))
_list_pull_requests = _w(scm.list_pull_requests, _R(name="list_pull_requests"))
_create_pull_request = _w(
    scm.create_pull_request,
    _R(name="create_pull_request", is_destructive=False, is_read_only=False),
)
_list_gitops_applications = _w(
    gitops.list_gitops_applications, _R(name="list_gitops_applications"),
)
_get_application_status = _w(
    gitops.get_application_status, _R(name="get_application_status"),
)
_sync_application = _w(
    gitops.sync_application,
    _R(name="sync_application", is_destructive=True, is_read_only=False),
)
_rollback_application = _w(
    gitops.rollback_application,
    _R(name="rollback_application", is_destructive=True, is_read_only=False),
)
_get_deployment_history = _w(
    gitops.get_deployment_history, _R(name="get_deployment_history"),
)
_list_environments = _w(infra.list_environments, _R(name="list_environments"))
_get_plan_output = _w(infra.get_plan_output, _R(name="get_plan_output"))
_create_service_pr = _w(
    infra.create_service_pr,
    _R(name="create_service_pr", is_destructive=False, is_read_only=False),
)
_approve_and_merge = _w(
    infra.approve_and_merge,
    _R(name="approve_and_merge", is_destructive=True, is_read_only=False),
)
_list_templates = _w(scaffold.list_templates, _R(name="list_templates"))
_scaffold_project = _w(
    scaffold.scaffold_project,
    _R(name="scaffold_project", is_destructive=False, is_read_only=False),
)
_get_dora_metrics = _w(metrics.get_dora_metrics, _R(name="get_dora_metrics"))
_get_team_metrics = _w(metrics.get_team_metrics, _R(name="get_team_metrics"))
_compare_repos = _w(metrics.compare_repos, _R(name="compare_repos"))
_get_metric_trends = _w(metrics.get_metric_trends, _R(name="get_metric_trends"))
_get_dora_recommendations = _w(
    metrics.get_dora_recommendations, _R(name="get_dora_recommendations"),
)


@mcp.tool(
    name="list_pipeline_runs",
    description=(
        "List recent CI/CD pipeline runs for a repository. Supports GitHub Actions and Tekton."
    ),
    annotations=READ_ONLY,
)
async def list_pipeline_runs(repo: str, ci_provider: str = "auto") -> dict:
    """List recent CI/CD pipeline runs.

    Args:
        repo: Repository name (e.g., 'sample-nextjs-app').
        ci_provider: 'github', 'tekton', or 'auto' (detect from config).
    """
    return await _list_pipeline_runs(repo, ci_provider)


@mcp.tool(
    name="get_pipeline_status",
    description=(
        "Get the latest CI/CD pipeline status for a repo branch. "
        "Shows if the build passed, failed, or is still running."
    ),
    annotations=READ_ONLY,
)
async def get_pipeline_status(repo: str, branch: str = "main") -> dict:
    """Get latest pipeline status.

    Args:
        repo: Repository name.
        branch: Branch to check (default: 'main').
    """
    return await _get_pipeline_status(repo, branch)


@mcp.tool(
    name="trigger_pipeline",
    description=(
        "Trigger a CI/CD pipeline run. WRITE operation — "
        "production environments require human approval."
    ),
    annotations=WRITE_SAFE,
)
async def trigger_pipeline(
    repo: str,
    workflow: str,
    ref: str = "main",
    environment: str = "",
    ci_provider: str = "auto",
) -> dict:
    """Trigger a CI/CD pipeline.

    Args:
        repo: Repository name.
        workflow: Workflow name (e.g., 'ci.yml', 'deploy.yml').
        ref: Git ref to run against.
        environment: Target environment (dev/staging/prod).
        ci_provider: 'github', 'tekton', or 'auto'.
    """
    return await _trigger_pipeline(repo, workflow, ref, environment, ci_provider)


@mcp.tool(
    name="cancel_pipeline",
    description="Cancel a running CI/CD pipeline.",
    annotations=WRITE_DESTRUCTIVE,
)
async def cancel_pipeline(repo: str, run_id: str) -> dict:
    """Cancel a running pipeline.

    Args:
        repo: Repository name.
        run_id: Pipeline run ID to cancel.
    """
    return await _cancel_pipeline(repo, run_id)


# ---------------------------------------------------------------------------
# Source Control Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="list_repositories",
    description="List all repositories in the GitHub organization.",
    annotations=READ_ONLY,
)
async def list_repositories(org: str = "") -> dict:
    """List repositories.

    Args:
        org: Organization name (uses default from config if empty).
    """
    return await _list_repositories(org)


@mcp.tool(
    name="get_repository_info",
    description="Get detailed information about a specific repository.",
    annotations=READ_ONLY,
)
async def get_repository_info(repo: str) -> dict:
    """Get repository details.

    Args:
        repo: Repository name (e.g., 'sample-nextjs-app').
    """
    return await _get_repository_info(repo)


@mcp.tool(
    name="list_pull_requests",
    description="List pull requests for a repository, filtered by state.",
    annotations=READ_ONLY,
)
async def list_pull_requests(repo: str, state: str = "open") -> dict:
    """List pull requests.

    Args:
        repo: Repository name.
        state: 'open', 'closed', or 'all'.
    """
    return await _list_pull_requests(repo, state)


@mcp.tool(
    name="create_pull_request",
    description=(
        "Create a new pull request in a repository. WRITE operation — creates a visible PR."
    ),
    annotations=WRITE_SAFE,
)
async def create_pull_request(
    repo: str,
    title: str,
    body: str,
    head_branch: str,
    base_branch: str = "main",
) -> dict:
    """Create a pull request.

    Args:
        repo: Repository name.
        title: PR title (under 70 chars).
        body: PR description in markdown.
        head_branch: Source branch with changes.
        base_branch: Target branch (default: 'main').
    """
    return await _create_pull_request(repo, title, body, head_branch, base_branch)


# ---------------------------------------------------------------------------
# GitOps Tools (ArgoCD)
# ---------------------------------------------------------------------------


@mcp.tool(
    name="list_gitops_applications",
    description="List all ArgoCD-managed applications with sync and health status.",
    annotations=READ_ONLY,
)
async def list_gitops_applications(project: str = "", namespace: str = "") -> dict:
    """List GitOps applications.

    Args:
        project: Filter by ArgoCD project (empty = all).
        namespace: Filter by K8s namespace (empty = all).
    """
    return await _list_gitops_applications(project, namespace)


@mcp.tool(
    name="get_application_status",
    description="Get detailed status of a GitOps application including sync, health, and images.",
    annotations=READ_ONLY,
)
async def get_application_status(app_name: str) -> dict:
    """Get application status.

    Args:
        app_name: ArgoCD application name.
    """
    return await _get_application_status(app_name)


@mcp.tool(
    name="sync_application",
    description=(
        "Trigger a sync (deployment) for a GitOps application. "
        "WRITE operation — production requires approval. "
        "WARNING: prune=True deletes resources not in git."
    ),
    annotations=WRITE_DESTRUCTIVE,
)
async def sync_application(app_name: str, revision: str = "", prune: bool = False) -> dict:
    """Sync a GitOps application.

    Args:
        app_name: ArgoCD application name.
        revision: Specific git revision (empty = HEAD).
        prune: Delete resources not in git. DANGEROUS.
    """
    return await _sync_application(app_name, revision, prune)


@mcp.tool(
    name="rollback_application",
    description=(
        "Rollback a GitOps application to a previous deployment. "
        "Use get_deployment_history to find the revision_id."
    ),
    annotations=WRITE_DESTRUCTIVE,
)
async def rollback_application(app_name: str, revision_id: int) -> dict:
    """Rollback an application.

    Args:
        app_name: ArgoCD application name.
        revision_id: History revision ID to rollback to.
    """
    return await _rollback_application(app_name, revision_id)


@mcp.tool(
    name="get_deployment_history",
    description="Get deployment history for a GitOps application.",
    annotations=READ_ONLY,
)
async def get_deployment_history(app_name: str, limit: int = 10) -> dict:
    """Get deployment history.

    Args:
        app_name: ArgoCD application name.
        limit: Max entries to return.
    """
    return await _get_deployment_history(app_name, limit)


# ---------------------------------------------------------------------------
# Infrastructure Tools (Terraform via GitOps)
# ---------------------------------------------------------------------------


@mcp.tool(
    name="list_environments",
    description=(
        "List available infrastructure environments (dev/staging/prod) "
        "with golden path defaults for scaling and security."
    ),
    annotations=READ_ONLY,
)
async def list_environments() -> dict:
    """List environment configurations."""
    return await _list_environments()


@mcp.tool(
    name="get_plan_output",
    description=(
        "Read Terraform plan output from a PR's comments. "
        "The CI workflow posts plan output after terraform plan runs."
    ),
    annotations=READ_ONLY,
)
async def get_plan_output(pr_number: int) -> dict:
    """Read Terraform plan from PR comments.

    Args:
        pr_number: PR number in platform-terraform.
    """
    return await _get_plan_output(pr_number)


@mcp.tool(
    name="create_service_pr",
    description=(
        "Create a PR to provision a new Cloud Run service via Terraform. "
        "Generates config from golden path template, commits to branch, opens PR. "
        "WRITE operation — creates a branch and PR."
    ),
    annotations=WRITE_SAFE,
)
async def create_service_pr(
    service_name: str,
    team: str,
    cost_center: str,
    environment: str = "dev",
    port: int = 8080,
    cpu: str = "",
    memory: str = "",
) -> dict:
    """Create infrastructure PR.

    Args:
        service_name: Service name (lowercase, hyphens, 3-63 chars).
        team: Owning team (e.g., 'marketing-web').
        cost_center: Finance cost center (e.g., 'MKT-40210').
        environment: 'dev', 'staging', or 'prod'.
        port: Container port (default: 8080).
        cpu: CPU allocation (empty = environment default).
        memory: Memory (empty = environment default).
    """
    return await _create_service_pr(
        service_name, team, cost_center, environment, port, cpu, memory
    )


@mcp.tool(
    name="approve_and_merge",
    description=(
        "Merge a Terraform PR to trigger apply. DESTRUCTIVE — changes real infrastructure. "
        "Separation of duties: approver must differ from PR author."
    ),
    annotations=WRITE_DESTRUCTIVE,
)
async def approve_and_merge(pr_number: int, approver: str = "") -> dict:
    """Merge a Terraform PR.

    Args:
        pr_number: PR number to merge.
        approver: GitHub username of approver (must differ from PR author).
    """
    return await _approve_and_merge(pr_number, approver)


# ---------------------------------------------------------------------------
# Scaffold Tools (Project Creation)
# ---------------------------------------------------------------------------


@mcp.tool(
    name="list_templates",
    description=(
        "List available project templates (golden paths). "
        "Shows supported languages, frameworks, and what gets generated."
    ),
    annotations=READ_ONLY,
)
async def list_templates() -> dict:
    """List project templates.

    Returns available archetypes (Python/FastAPI, Node/Next.js, Java/Spring Boot).
    """
    return await _list_templates()


@mcp.tool(
    name="scaffold_project",
    description=(
        "Create a new project from scratch — repo, code, CI, and infrastructure in one action. "
        "WRITE operation — creates a GitHub repository and opens a Terraform PR."
    ),
    annotations=WRITE_SAFE,
)
async def scaffold_project(
    service_name: str,
    template: str,
    team: str,
    cost_center: str,
    description: str = "",
    environment: str = "dev",
    private: bool = False,
) -> dict:
    """Scaffold a complete project.

    Args:
        service_name: Service name (lowercase, hyphens, 3-63 chars).
        template: 'python-fastapi', 'node-nextjs', or 'java-spring'.
        team: Owning team (e.g., 'marketing-web').
        cost_center: Finance cost center (e.g., 'MKT-40210').
        description: Optional repo description.
        environment: Initial environment — 'dev', 'staging', or 'prod'.
        private: Whether repo is private (default: public).
    """
    return await _scaffold_project(
        service_name, template, team, cost_center, description, environment, private
    )


# ---------------------------------------------------------------------------
# DORA Metrics Tools
# ---------------------------------------------------------------------------


@mcp.tool(
    name="get_dora_metrics",
    description=(
        "Get DORA metrics for a repository — deployment frequency, lead time, "
        "change failure rate, MTTR, and AI rework rate. Each metric graded "
        "against industry benchmarks (Elite/High/Medium/Low)."
    ),
    annotations=READ_ONLY,
)
async def get_dora_metrics(repo: str, days: int = 30) -> dict:
    """Get DORA metrics for a single repository.

    Args:
        repo: Repository name (e.g., 'sample-nextjs-app').
        days: Analysis period in days (default: 30).
    """
    return await _get_dora_metrics(repo, days)


@mcp.tool(
    name="get_team_metrics",
    description=(
        "Get aggregated DORA metrics for a team across multiple repositories. "
        "Calculates per-repo and team-level rollup scores."
    ),
    annotations=READ_ONLY,
)
async def get_team_metrics(repos: str, days: int = 30) -> dict:
    """Get team-level DORA metrics.

    Args:
        repos: Comma-separated repository names (e.g., 'repo-a,repo-b,repo-c').
        days: Analysis period in days (default: 30).
    """
    return await _get_team_metrics(repos, days)


@mcp.tool(
    name="compare_repos",
    description=(
        "Compare DORA metrics side-by-side across repositories, "
        "highlighting strongest and weakest performers."
    ),
    annotations=READ_ONLY,
)
async def compare_repos(repos: str, days: int = 30) -> dict:
    """Compare DORA metrics across repos.

    Args:
        repos: Comma-separated repository names (at least 2).
        days: Analysis period in days (default: 30).
    """
    return await _compare_repos(repos, days)


@mcp.tool(
    name="get_metric_trends",
    description=(
        "Get DORA metric trends over multiple time periods. "
        "Shows improvement or degradation patterns."
    ),
    annotations=READ_ONLY,
)
async def get_metric_trends(repo: str, periods: int = 3, period_days: int = 30) -> dict:
    """Get DORA metric trends.

    Args:
        repo: Repository name.
        periods: Number of periods to compare (default: 3).
        period_days: Length of each period in days (default: 30).
    """
    return await _get_metric_trends(repo, periods, period_days)


@mcp.tool(
    name="get_dora_recommendations",
    description=(
        "Get improvement recommendations based on weakest DORA metrics. "
        "Actionable suggestions prioritized by impact."
    ),
    annotations=READ_ONLY,
)
async def get_dora_recommendations(repo: str) -> dict:
    """Get DORA improvement recommendations.

    Args:
        repo: Repository name to analyze.
    """
    return await _get_dora_recommendations(repo)


# ---------------------------------------------------------------------------
# MCP Resources — Ford knowledge base (tribal knowledge for any MCP client)
# ---------------------------------------------------------------------------


@mcp.resource(
    "ford://knowledge/index",
    name="ford-knowledge-index",
    title="Ford Knowledge Index",
    description="List all available practices, guardrails, and runbooks.",
    mime_type="application/json",
)
def knowledge_index() -> str:
    return list_knowledge_files()


@mcp.resource(
    "ford://knowledge/practices/{name}",
    name="ford-practice",
    title="Ford Platform Practice",
    description="Ford's platform engineering practices and standards (YAML).",
    mime_type="text/yaml",
)
def get_practice(name: str) -> str:
    return read_knowledge_file("practices", name)


@mcp.resource(
    "ford://knowledge/guardrails/{name}",
    name="ford-guardrail",
    title="Ford Guardrail Rule",
    description="Ford's guardrail rules for environment protection and approval gates (YAML).",
    mime_type="text/yaml",
)
def get_guardrail(name: str) -> str:
    return read_knowledge_file("guardrails", name)


@mcp.resource(
    "ford://knowledge/runbooks/{name}",
    name="ford-runbook",
    title="Ford Runbook",
    description="Ford's operational runbooks for incident response and troubleshooting.",
    mime_type="text/markdown",
)
def get_runbook(name: str) -> str:
    return read_knowledge_file("runbooks", name)


# ---------------------------------------------------------------------------
# MCP Prompts — structured workflows from runbooks
# ---------------------------------------------------------------------------


@mcp.prompt(
    name="incident-response",
    title="Incident Response Runbook",
    description=(
        "Ford incident response runbook for Cloud Run outages, "
        "ArgoCD sync issues, and stuck pipelines."
    ),
)
def incident_response_prompt() -> str:
    runbook = read_knowledge_file("runbooks", "incident-response")
    return (
        "I need help with an incident. Use the following runbook "
        "to guide the troubleshooting:\n\n"
        f"{runbook}"
    )


@mcp.prompt(
    name="deployment-review",
    title="Deployment Review Checklist",
    description=(
        "Pre-deployment review checklist based on Ford's deployment "
        "standards and approval gates."
    ),
)
def deployment_review_prompt(environment: str = "prod") -> str:
    standards = read_knowledge_file("practices", "deployment-standards")
    gates = read_knowledge_file("guardrails", "approval-gates")
    return (
        f"I'm about to deploy to {environment}. Review the following "
        f"standards and approval gates, then give me a checklist of "
        f"what I need to verify before proceeding:\n\n"
        f"## Deployment Standards\n```yaml\n{standards}\n```\n\n"
        f"## Approval Gates\n```yaml\n{gates}\n```"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_mcp(transport: str = "stdio", host: str = "0.0.0.0", port: int = 8080) -> None:
    """Start the MCP server."""
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    run_mcp()
