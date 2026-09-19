"""MCP Server — exposes all Ford Platform Agent tools via Model Context Protocol.

Any MCP-compatible client (Claude Code, VS Code Copilot, Cursor, etc.) can
discover and invoke these tools over stdio or HTTP transport.

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

from ford_platform_agent.config import ArgoCDConfig, GitHubConfig, TektonConfig
from ford_platform_agent.providers.registry import ProviderRegistry
from ford_platform_agent.tools import cicd, gitops, infra, scm

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
    try:
        yield {"registry": registry}
    finally:
        await registry.close_all()


mcp = MCPServer(
    name="ford-platform-agent",
    title="Ford Platform Agent",
    description=(
        "AI-powered platform engineering tools for CI/CD, source control, "
        "GitOps deployments, and infrastructure provisioning."
    ),
    version="0.1.0",
    lifespan=_lifespan,
)

# ---------------------------------------------------------------------------
# CI/CD Tools
# ---------------------------------------------------------------------------

READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
WRITE_SAFE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)
WRITE_DESTRUCTIVE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=False)


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
    return await cicd.list_pipeline_runs(repo, ci_provider)


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
    return await cicd.get_pipeline_status(repo, branch)


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
    return await cicd.trigger_pipeline(repo, workflow, ref, environment, ci_provider)


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
    return await cicd.cancel_pipeline(repo, run_id)


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
    return await scm.list_repositories(org)


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
    return await scm.get_repository_info(repo)


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
    return await scm.list_pull_requests(repo, state)


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
    return await scm.create_pull_request(repo, title, body, head_branch, base_branch)


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
    return await gitops.list_gitops_applications(project, namespace)


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
    return await gitops.get_application_status(app_name)


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
    return await gitops.sync_application(app_name, revision, prune)


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
    return await gitops.rollback_application(app_name, revision_id)


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
    return await gitops.get_deployment_history(app_name, limit)


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
    return await infra.list_environments()


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
    return await infra.get_plan_output(pr_number)


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
    return await infra.create_service_pr(
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
    return await infra.approve_and_merge(pr_number, approver)


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
