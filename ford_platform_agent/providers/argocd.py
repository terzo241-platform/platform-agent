"""ArgoCD provider — implements GitOpsProvider for ArgoCD API."""

from __future__ import annotations

import httpx
import structlog

from ford_platform_agent.config import ArgoCDConfig
from ford_platform_agent.providers.base import Application, SyncStatus

logger = structlog.get_logger()

_SYNC_STATUS_MAP = {
    "Synced": SyncStatus.SYNCED,
    "OutOfSync": SyncStatus.OUT_OF_SYNC,
    "Unknown": SyncStatus.UNKNOWN,
}

_HEALTH_STATUS_MAP = {
    "Healthy": SyncStatus.HEALTHY,
    "Progressing": SyncStatus.PROGRESSING,
    "Degraded": SyncStatus.DEGRADED,
    "Missing": SyncStatus.MISSING,
    "Unknown": SyncStatus.UNKNOWN,
}


class ArgoCDProvider:
    """ArgoCD GitOps provider."""

    def __init__(self, config: ArgoCDConfig | None = None) -> None:
        self._config = config or ArgoCDConfig()
        self._client = httpx.AsyncClient(
            base_url=self._config.server.rstrip("/"),
            headers={
                "Authorization": f"Bearer {self._config.token}",
                "Content-Type": "application/json",
            },
            verify=not self._config.insecure,
            timeout=30.0,
        )

    @property
    def name(self) -> str:
        return "argocd"

    async def _get(self, path: str, params: dict | None = None) -> dict:
        resp = await self._client.get(f"/api/v1{path}", params=params)
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, json: dict | None = None) -> dict:
        resp = await self._client.post(f"/api/v1{path}", json=json or {})
        resp.raise_for_status()
        return resp.json()

    async def list_applications(
        self, project: str | None = None, namespace: str | None = None
    ) -> list[Application]:
        params: dict = {}
        if project:
            params["projects"] = [project]
        if namespace:
            params["appNamespace"] = namespace
        data = await self._get("/applications", params)
        return [self._to_application(item) for item in data.get("items", [])]

    async def get_application(self, app_name: str) -> Application:
        data = await self._get(f"/applications/{app_name}")
        return self._to_application(data)

    async def sync_application(
        self, app_name: str, revision: str | None = None, prune: bool = False
    ) -> Application:
        payload: dict = {"prune": prune}
        if revision:
            payload["revision"] = revision
        data = await self._post(f"/applications/{app_name}/sync", payload)
        logger.info(
            "argocd_sync_triggered",
            app=app_name,
            revision=revision,
            prune=prune,
            provider="argocd",
        )
        return self._to_application(data)

    async def get_sync_status(self, app_name: str) -> SyncStatus:
        app = await self.get_application(app_name)
        return app.sync_status

    async def rollback_application(self, app_name: str, revision_id: int) -> Application:
        data = await self._post(
            f"/applications/{app_name}/rollback",
            {"id": revision_id},
        )
        logger.info(
            "argocd_rollback_triggered",
            app=app_name,
            revision_id=revision_id,
            provider="argocd",
        )
        return self._to_application(data)

    async def get_application_history(self, app_name: str, limit: int = 10) -> list[dict]:
        data = await self._get(f"/applications/{app_name}")
        history = data.get("status", {}).get("history", [])
        entries = []
        for h in history[-limit:]:
            entries.append(
                {
                    "id": h.get("id"),
                    "revision": h.get("revision", "")[:12],
                    "deployed_at": h.get("deployedAt", ""),
                    "source": h.get("source", {}).get("path", ""),
                }
            )
        return entries

    def _to_application(self, data: dict) -> Application:
        metadata = data.get("metadata", {})
        spec = data.get("spec", {})
        status = data.get("status", {})
        source = spec.get("source", spec.get("sources", [{}])[0] if spec.get("sources") else {})
        sync = status.get("sync", {})
        health = status.get("health", {})

        images = []
        for s in status.get("summary", {}).get("images", []):
            images.append(s)

        return Application(
            name=metadata.get("name", ""),
            namespace=metadata.get("namespace", "argocd"),
            project=spec.get("project", "default"),
            repo_url=source.get("repoURL", ""),
            path=source.get("path", ""),
            target_revision=source.get("targetRevision", "HEAD"),
            sync_status=_SYNC_STATUS_MAP.get(sync.get("status", ""), SyncStatus.UNKNOWN),
            health_status=_HEALTH_STATUS_MAP.get(health.get("status", ""), SyncStatus.UNKNOWN),
            current_revision=sync.get("revision", "")[:12],
            images=images,
        )

    async def close(self) -> None:
        await self._client.aclose()
