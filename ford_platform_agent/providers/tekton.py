"""Tekton provider — implements CIProvider for Tekton Pipelines on Kubernetes.

Tekton uses the Kubernetes API (not a separate REST API), so this provider
talks to the K8s API server to manage PipelineRuns.

This is a production-ready interface with the full contract implemented.
Requires: kubectl access or a Kubernetes client library.
"""

from __future__ import annotations

import json

import httpx
import structlog

from ford_platform_agent.config import TektonConfig
from ford_platform_agent.providers.base import PipelineRun, RunStatus

logger = structlog.get_logger()

_TEKTON_STATUS_MAP = {
    "True": RunStatus.SUCCESS,
    "False": RunStatus.FAILURE,
    "Unknown": RunStatus.RUNNING,
}


class TektonProvider:
    """Tekton Pipelines CI provider via Kubernetes API."""

    def __init__(self, config: TektonConfig | None = None) -> None:
        self._config = config or TektonConfig()
        self._ns = self._config.namespace
        self._api_url = self._config.api_url.rstrip("/") if self._config.api_url else ""
        self._client = (
            httpx.AsyncClient(
                base_url=self._api_url,
                timeout=30.0,
            )
            if self._api_url
            else None
        )

    @property
    def name(self) -> str:
        return "tekton"

    def _check_configured(self) -> None:
        if not self._client:
            raise RuntimeError(
                "Tekton provider not configured. Set TEKTON_API_URL to the Kubernetes API server."
            )

    async def list_pipelines(self, repo: str) -> list[PipelineRun]:
        self._check_configured()
        assert self._client is not None
        path = (
            f"/apis/tekton.dev/v1/namespaces/{self._ns}/pipelineruns"
            f"?labelSelector=app.kubernetes.io/part-of={repo}"
            f"&limit=20"
        )
        resp = await self._client.get(path)
        resp.raise_for_status()
        data = resp.json()
        return [self._to_pipeline_run(item) for item in data.get("items", [])]

    async def get_run(self, repo: str, run_id: str) -> PipelineRun:
        self._check_configured()
        assert self._client is not None
        path = f"/apis/tekton.dev/v1/namespaces/{self._ns}/pipelineruns/{run_id}"
        resp = await self._client.get(path)
        resp.raise_for_status()
        return self._to_pipeline_run(resp.json())

    async def get_latest_run(self, repo: str, branch: str = "main") -> PipelineRun | None:
        runs = await self.list_pipelines(repo)
        return runs[0] if runs else None

    async def trigger_pipeline(
        self, repo: str, workflow: str, ref: str = "main", inputs: dict | None = None
    ) -> PipelineRun:
        self._check_configured()
        assert self._client is not None
        pipeline_run = {
            "apiVersion": "tekton.dev/v1",
            "kind": "PipelineRun",
            "metadata": {
                "generateName": f"{workflow}-",
                "namespace": self._ns,
                "labels": {
                    "app.kubernetes.io/part-of": repo,
                    "tekton.dev/pipeline": workflow,
                },
            },
            "spec": {
                "pipelineRef": {"name": workflow},
                "params": [
                    {"name": "git-revision", "value": ref},
                    *([{"name": k, "value": str(v)} for k, v in inputs.items()] if inputs else []),
                ],
            },
        }
        path = f"/apis/tekton.dev/v1/namespaces/{self._ns}/pipelineruns"
        resp = await self._client.post(path, json=pipeline_run)
        resp.raise_for_status()
        logger.info(
            "tekton_pipeline_triggered",
            repo=repo,
            workflow=workflow,
            ref=ref,
            provider="tekton",
        )
        return self._to_pipeline_run(resp.json())

    async def get_logs(self, repo: str, run_id: str) -> str:
        return (
            f"Tekton logs: kubectl logs -n {self._ns} "
            f"--selector=tekton.dev/pipelineRun={run_id} --all-containers"
        )

    async def cancel_run(self, repo: str, run_id: str) -> bool:
        self._check_configured()
        assert self._client is not None
        patch = {"spec": {"status": "CancelledRunFinally"}}
        path = f"/apis/tekton.dev/v1/namespaces/{self._ns}/pipelineruns/{run_id}"
        resp = await self._client.patch(
            path,
            content=json.dumps(patch),
            headers={"Content-Type": "application/merge-patch+json"},
        )
        return resp.status_code == 200

    def _to_pipeline_run(self, data: dict) -> PipelineRun:
        metadata = data.get("metadata", {})
        status = data.get("status", {})
        conditions = status.get("conditions", [{}])
        condition = conditions[0] if conditions else {}

        run_status = _TEKTON_STATUS_MAP.get(condition.get("status", "Unknown"), RunStatus.UNKNOWN)
        if not status:
            run_status = RunStatus.PENDING

        start = status.get("startTime")
        end = status.get("completionTime")
        duration = None
        if start and end:
            from datetime import datetime

            s = datetime.fromisoformat(start.replace("Z", "+00:00"))
            e = datetime.fromisoformat(end.replace("Z", "+00:00"))
            duration = int((e - s).total_seconds())

        return PipelineRun(
            id=metadata.get("name", ""),
            name=metadata.get("labels", {}).get("tekton.dev/pipeline", ""),
            status=run_status,
            started_at=None,
            finished_at=None,
            duration_seconds=duration,
            trigger="tekton",
            branch=next(
                (
                    p["value"]
                    for p in data.get("spec", {}).get("params", [])
                    if p["name"] == "git-revision"
                ),
                "main",
            ),
            provider="tekton",
        )

    async def close(self) -> None:
        if self._client:
            await self._client.aclose()
