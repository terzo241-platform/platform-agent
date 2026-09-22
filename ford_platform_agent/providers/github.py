"""GitHub provider — implements CIProvider and SCMProvider for GitHub Actions + API."""

from __future__ import annotations

from datetime import datetime

import httpx
import structlog

from ford_platform_agent.config import GitHubConfig
from ford_platform_agent.providers.base import (
    PipelineRun,
    PullRequest,
    Repository,
    RunStatus,
)

logger = structlog.get_logger()

_GHA_STATUS_MAP = {
    "completed": {
        "success": RunStatus.SUCCESS,
        "failure": RunStatus.FAILURE,
        "cancelled": RunStatus.CANCELLED,
    },
    "in_progress": RunStatus.RUNNING,
    "queued": RunStatus.PENDING,
    "requested": RunStatus.PENDING,
    "waiting": RunStatus.PENDING,
}


def _parse_run_status(status: str, conclusion: str | None) -> RunStatus:
    mapping = _GHA_STATUS_MAP.get(status)
    if isinstance(mapping, dict):
        return mapping.get(conclusion or "", RunStatus.UNKNOWN)
    if isinstance(mapping, RunStatus):
        return mapping
    return RunStatus.UNKNOWN


def _parse_dt(val: str | None) -> datetime | None:
    if not val:
        return None
    return datetime.fromisoformat(val.replace("Z", "+00:00"))


class GitHubProvider:
    """GitHub Actions (CI) + GitHub API (SCM) provider."""

    def __init__(self, config: GitHubConfig | None = None) -> None:
        self._config = config or GitHubConfig()
        self._client = httpx.AsyncClient(
            base_url=self._config.api_url,
            headers={
                "Authorization": f"Bearer {self._config.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=30.0,
        )
        self._org = self._config.org

    @property
    def name(self) -> str:
        return "github"

    async def _get(self, path: str, params: dict | None = None) -> dict | list:
        resp = await self._client.get(path, params=params)
        resp.raise_for_status()
        return resp.json()

    async def _post(self, path: str, json: dict | None = None) -> dict:
        resp = await self._client.post(path, json=json or {})
        resp.raise_for_status()
        return resp.json()

    def _repo_path(self, repo: str) -> str:
        if "/" in repo:
            return f"/repos/{repo}"
        return f"/repos/{self._org}/{repo}"

    # --- CIProvider ---

    async def list_pipelines(self, repo: str) -> list[PipelineRun]:
        data = await self._get(f"{self._repo_path(repo)}/actions/runs", {"per_page": 20})
        return [self._to_pipeline_run(r) for r in data.get("workflow_runs", [])]

    async def get_run(self, repo: str, run_id: str) -> PipelineRun:
        data = await self._get(f"{self._repo_path(repo)}/actions/runs/{run_id}")
        return self._to_pipeline_run(data)

    async def get_latest_run(self, repo: str, branch: str = "main") -> PipelineRun | None:
        data = await self._get(
            f"{self._repo_path(repo)}/actions/runs",
            {"branch": branch, "per_page": 1},
        )
        runs = data.get("workflow_runs", [])
        return self._to_pipeline_run(runs[0]) if runs else None

    async def trigger_pipeline(
        self, repo: str, workflow: str, ref: str = "main", inputs: dict | None = None
    ) -> PipelineRun:
        path = f"{self._repo_path(repo)}/actions/workflows/{workflow}/dispatches"
        payload: dict = {"ref": ref}
        if inputs:
            payload["inputs"] = inputs
        await self._client.post(path, json=payload)

        logger.info(
            "pipeline_triggered",
            repo=repo,
            workflow=workflow,
            ref=ref,
            provider="github",
        )

        latest = await self.get_latest_run(repo, branch=ref)
        return latest or PipelineRun(
            id="pending",
            name=workflow,
            status=RunStatus.PENDING,
            provider="github",
        )

    async def get_logs(self, repo: str, run_id: str) -> str:
        resp = await self._client.get(
            f"{self._repo_path(repo)}/actions/runs/{run_id}/logs",
            follow_redirects=True,
        )
        if resp.status_code == 200:
            return f"Logs available at: {resp.url} (binary zip, {len(resp.content)} bytes)"
        return f"Logs not available (HTTP {resp.status_code})"

    async def cancel_run(self, repo: str, run_id: str) -> bool:
        resp = await self._client.post(f"{self._repo_path(repo)}/actions/runs/{run_id}/cancel")
        return resp.status_code == 202

    # --- SCMProvider ---

    async def list_repos(self, org: str | None = None) -> list[Repository]:
        target = org or self._org
        data = await self._get(f"/orgs/{target}/repos", {"per_page": 100, "sort": "updated"})
        return [self._to_repository(r) for r in data]

    async def get_repo(self, repo: str) -> Repository:
        data = await self._get(self._repo_path(repo))
        return self._to_repository(data)

    async def create_repo(
        self,
        name: str,
        description: str = "",
        private: bool = False,
        auto_init: bool = True,
    ) -> Repository:
        data = await self._post(
            f"/orgs/{self._org}/repos",
            {
                "name": name,
                "description": description,
                "private": private,
                "auto_init": auto_init,
            },
        )
        logger.info("repo_created", repo=name, org=self._org)
        return self._to_repository(data)

    async def commit_files(
        self,
        repo: str,
        branch: str,
        message: str,
        files: dict[str, str],
    ) -> str:
        ref_data = await self._get(f"{self._repo_path(repo)}/git/ref/heads/{branch}")
        base_sha = ref_data["object"]["sha"]

        tree_entries = [
            {"path": path, "mode": "100644", "type": "blob", "content": content}
            for path, content in files.items()
        ]
        tree_resp = await self._post(
            f"{self._repo_path(repo)}/git/trees",
            {"base_tree": base_sha, "tree": tree_entries},
        )

        commit_resp = await self._post(
            f"{self._repo_path(repo)}/git/commits",
            {"message": message, "tree": tree_resp["sha"], "parents": [base_sha]},
        )
        commit_sha = commit_resp["sha"]

        await self._client.patch(
            f"{self._repo_path(repo)}/git/refs/heads/{branch}",
            json={"sha": commit_sha},
        )
        logger.info("files_committed", repo=repo, branch=branch, file_count=len(files))
        return commit_sha

    async def create_branch(self, repo: str, branch: str, from_ref: str = "main") -> str:
        ref_data = await self._get(f"{self._repo_path(repo)}/git/ref/heads/{from_ref}")
        sha = ref_data["object"]["sha"]
        await self._post(
            f"{self._repo_path(repo)}/git/refs",
            {"ref": f"refs/heads/{branch}", "sha": sha},
        )
        logger.info("branch_created", repo=repo, branch=branch, from_ref=from_ref)
        return sha

    async def create_pull_request(
        self, repo: str, title: str, body: str, head: str, base: str = "main"
    ) -> PullRequest:
        data = await self._post(
            f"{self._repo_path(repo)}/pulls",
            {"title": title, "body": body, "head": head, "base": base},
        )
        return self._to_pull_request(data)

    async def get_pull_request(self, repo: str, pr_number: int) -> PullRequest:
        data = await self._get(f"{self._repo_path(repo)}/pulls/{pr_number}")
        return self._to_pull_request(data)

    async def list_pull_requests(self, repo: str, state: str = "open") -> list[PullRequest]:
        data = await self._get(f"{self._repo_path(repo)}/pulls", {"state": state, "per_page": 20})
        return [self._to_pull_request(pr) for pr in data]

    # --- Converters ---

    def _to_pipeline_run(self, data: dict) -> PipelineRun:
        started = _parse_dt(data.get("run_started_at") or data.get("created_at"))
        finished = _parse_dt(data.get("updated_at")) if data.get("status") == "completed" else None
        duration = None
        if started and finished:
            duration = int((finished - started).total_seconds())

        return PipelineRun(
            id=str(data["id"]),
            name=data.get("name", data.get("display_title", "")),
            status=_parse_run_status(data.get("status", ""), data.get("conclusion")),
            url=data.get("html_url", ""),
            started_at=started,
            finished_at=finished,
            duration_seconds=duration,
            trigger=data.get("event", ""),
            branch=data.get("head_branch", ""),
            commit_sha=data.get("head_sha", "")[:8],
            provider="github",
        )

    def _to_repository(self, data: dict) -> Repository:
        return Repository(
            name=data["name"],
            full_name=data["full_name"],
            url=data["html_url"],
            default_branch=data.get("default_branch", "main"),
            description=data.get("description") or "",
            language=data.get("language") or "",
            ci_provider="github",
        )

    def _to_pull_request(self, data: dict) -> PullRequest:
        return PullRequest(
            number=data["number"],
            title=data["title"],
            url=data["html_url"],
            state=data["state"],
            branch=data.get("head", {}).get("ref", ""),
            author=data.get("user", {}).get("login", ""),
            created_at=_parse_dt(data.get("created_at")),
        )

    async def close(self) -> None:
        await self._client.aclose()
