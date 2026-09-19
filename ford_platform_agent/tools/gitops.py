"""GitOps tools — ArgoCD application management.

Provides deployment status, sync operations, and rollback through
the GitOpsProvider abstraction (currently ArgoCD, extensible to Flux).
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
        raise RuntimeError("ProviderRegistry not initialized.")
    return _registry


async def list_gitops_applications(project: str = "", namespace: str = "") -> dict:
    """List all GitOps-managed applications (ArgoCD).

    Args:
        project: Filter by ArgoCD project name. Leave empty for all projects.
        namespace: Filter by Kubernetes namespace. Leave empty for all.

    Returns:
        List of applications with sync status, health, and current revision.
    """
    reg = _get_registry()
    gitops = reg.get_gitops()
    apps = await gitops.list_applications(project or None, namespace or None)
    return {
        "provider": gitops.name,
        "total": len(apps),
        "applications": [
            {
                "name": a.name,
                "namespace": a.namespace,
                "project": a.project,
                "sync_status": a.sync_status.value,
                "health_status": a.health_status.value,
                "current_revision": a.current_revision,
                "repo_url": a.repo_url,
                "path": a.path,
                "images": a.images,
            }
            for a in apps
        ],
    }


async def get_application_status(app_name: str) -> dict:
    """Get detailed status of a GitOps application.

    Args:
        app_name: ArgoCD application name.

    Returns:
        Full application status including sync, health, revision, and images.
    """
    reg = _get_registry()
    gitops = reg.get_gitops()
    app = await gitops.get_application(app_name)
    return {
        "name": app.name,
        "namespace": app.namespace,
        "project": app.project,
        "sync_status": app.sync_status.value,
        "health_status": app.health_status.value,
        "current_revision": app.current_revision,
        "target_revision": app.target_revision,
        "repo_url": app.repo_url,
        "path": app.path,
        "images": app.images,
    }


async def sync_application(
    app_name: str, revision: str = "", prune: bool = False
) -> dict:
    """Trigger a sync (deployment) for a GitOps application.

    This is a WRITE operation. For production apps, human approval is required.
    WARNING: Setting prune=True will delete resources not in git — use with extreme caution.

    Args:
        app_name: ArgoCD application name.
        revision: Specific git revision to sync to. Leave empty for HEAD.
        prune: If True, delete resources that are no longer in git. DANGEROUS.

    Returns:
        Updated application status after sync trigger.
    """
    reg = _get_registry()
    gitops = reg.get_gitops()
    app = await gitops.sync_application(app_name, revision or None, prune)
    return {
        "name": app.name,
        "sync_status": app.sync_status.value,
        "health_status": app.health_status.value,
        "message": f"Sync triggered for {app_name}"
        + (f" at revision {revision}" if revision else ""),
    }


async def rollback_application(app_name: str, revision_id: int) -> dict:
    """Rollback a GitOps application to a previous deployment.

    This is a WRITE operation that changes the running deployment.
    Use get_deployment_history first to find the revision_id.

    Args:
        app_name: ArgoCD application name.
        revision_id: History revision ID to rollback to (from deployment history).

    Returns:
        Updated application status after rollback.
    """
    reg = _get_registry()
    gitops = reg.get_gitops()
    app = await gitops.rollback_application(app_name, revision_id)
    return {
        "name": app.name,
        "sync_status": app.sync_status.value,
        "message": f"Rollback triggered for {app_name} to revision {revision_id}",
    }


async def get_deployment_history(app_name: str, limit: int = 10) -> dict:
    """Get deployment history for a GitOps application.

    Args:
        app_name: ArgoCD application name.
        limit: Max number of history entries to return.

    Returns:
        List of past deployments with revision and timestamp.
    """
    reg = _get_registry()
    gitops = reg.get_gitops()
    history = await gitops.get_application_history(app_name, limit)
    return {
        "app_name": app_name,
        "total": len(history),
        "history": history,
    }
