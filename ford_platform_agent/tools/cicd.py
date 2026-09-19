"""CI/CD tools — provider-agnostic pipeline operations.

These functions are wrapped as ADK FunctionTools. The docstrings serve as
tool descriptions for the LLM, so they must be clear and specific.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ford_platform_agent.providers.registry import ProviderRegistry

_registry: ProviderRegistry | None = None


def set_registry(registry: ProviderRegistry) -> None:
    global _registry
    _registry = registry


def _get_registry() -> ProviderRegistry:
    if _registry is None:
        raise RuntimeError("ProviderRegistry not initialized. Call set_registry() first.")
    return _registry


async def list_pipeline_runs(repo: str, ci_provider: str = "auto") -> dict:
    """List recent CI/CD pipeline runs for a repository.

    Args:
        repo: Repository name (e.g., 'sample-nextjs-app' or 'org/repo').
        ci_provider: CI system — 'github', 'tekton', or 'auto' (detect from config).

    Returns:
        Dictionary with 'runs' list and 'provider' name.
    """
    reg = _get_registry()
    provider = reg.get_ci(repo) if ci_provider == "auto" else reg.get_ci(repo)
    if ci_provider == "tekton":
        provider = reg.tekton
    elif ci_provider == "github":
        provider = reg.github

    runs = await provider.list_pipelines(repo)
    return {
        "provider": provider.name,
        "total_runs": len(runs),
        "runs": [
            {
                "id": r.id,
                "name": r.name,
                "status": r.status.value,
                "branch": r.branch,
                "duration_seconds": r.duration_seconds,
                "url": r.url,
            }
            for r in runs[:10]
        ],
    }


async def get_pipeline_status(repo: str, branch: str = "main") -> dict:
    """Get the latest CI/CD pipeline status for a repository branch.

    Use this to check if the latest build passed, failed, or is still running.

    Args:
        repo: Repository name.
        branch: Branch to check (default: 'main').

    Returns:
        Latest run details including status, duration, and URL.
    """
    reg = _get_registry()
    ci = reg.get_ci(repo)
    run = await ci.get_latest_run(repo, branch)
    if not run:
        return {"status": "no_runs", "message": f"No pipeline runs found for {repo} on {branch}"}
    return {
        "provider": run.provider,
        "id": run.id,
        "name": run.name,
        "status": run.status.value,
        "branch": run.branch,
        "commit": run.commit_sha,
        "duration_seconds": run.duration_seconds,
        "url": run.url,
        "trigger": run.trigger,
    }


async def trigger_pipeline(
    repo: str,
    workflow: str,
    ref: str = "main",
    environment: str = "",
    ci_provider: str = "auto",
) -> dict:
    """Trigger a CI/CD pipeline run for a repository.

    This is a WRITE operation. For production environments, human approval is required.

    Args:
        repo: Repository name.
        workflow: Workflow/pipeline name to trigger (e.g., 'ci.yml', 'deploy.yml').
        ref: Git ref (branch or tag) to run against.
        environment: Target environment (dev/staging/prod). Used for guardrail checks.
        ci_provider: Which CI system — 'github', 'tekton', or 'auto'.

    Returns:
        Triggered run details.
    """
    reg = _get_registry()
    provider = reg.get_ci(repo) if ci_provider == "auto" else reg.get_ci(repo)
    if ci_provider == "tekton":
        provider = reg.tekton
    elif ci_provider == "github":
        provider = reg.github

    inputs = {}
    if environment:
        inputs["environment"] = environment

    run = await provider.trigger_pipeline(repo, workflow, ref, inputs or None)
    return {
        "provider": provider.name,
        "id": run.id,
        "status": run.status.value,
        "message": f"Pipeline '{workflow}' triggered on {repo} (ref: {ref})",
    }


async def cancel_pipeline(repo: str, run_id: str) -> dict:
    """Cancel a running CI/CD pipeline.

    Args:
        repo: Repository name.
        run_id: Pipeline run ID to cancel.

    Returns:
        Cancellation result.
    """
    reg = _get_registry()
    ci = reg.get_ci(repo)
    success = await ci.cancel_run(repo, run_id)
    return {
        "cancelled": success,
        "run_id": run_id,
        "message": "Pipeline cancelled" if success else "Failed to cancel pipeline",
    }
