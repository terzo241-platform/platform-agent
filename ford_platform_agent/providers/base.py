"""Protocol definitions for provider abstraction.

Each protocol defines the contract that a provider must implement.
The agent calls these interfaces; it never knows which backend is running.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from datetime import datetime


class RunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class SyncStatus(StrEnum):
    SYNCED = "synced"
    OUT_OF_SYNC = "out_of_sync"
    PROGRESSING = "progressing"
    DEGRADED = "degraded"
    HEALTHY = "healthy"
    MISSING = "missing"
    UNKNOWN = "unknown"


@dataclass
class PipelineRun:
    id: str
    name: str
    status: RunStatus
    url: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_seconds: int | None = None
    trigger: str = ""
    branch: str = ""
    commit_sha: str = ""
    provider: str = ""


@dataclass
class Repository:
    name: str
    full_name: str
    url: str
    default_branch: str = "main"
    description: str = ""
    language: str = ""
    ci_provider: str = ""


@dataclass
class PullRequest:
    number: int
    title: str
    url: str
    state: str = "open"
    branch: str = ""
    author: str = ""
    created_at: datetime | None = None


@dataclass
class Application:
    name: str
    namespace: str
    project: str
    repo_url: str
    path: str
    target_revision: str = "HEAD"
    sync_status: SyncStatus = SyncStatus.UNKNOWN
    health_status: SyncStatus = SyncStatus.UNKNOWN
    current_revision: str = ""
    last_synced_at: datetime | None = None
    images: list[str] = field(default_factory=list)


@runtime_checkable
class CIProvider(Protocol):
    """Contract for CI/CD pipeline providers (GitHub Actions, Tekton, Jenkins)."""

    @property
    def name(self) -> str: ...

    async def list_pipelines(self, repo: str) -> list[PipelineRun]: ...

    async def get_run(self, repo: str, run_id: str) -> PipelineRun: ...

    async def get_latest_run(self, repo: str, branch: str = "main") -> PipelineRun | None: ...

    async def trigger_pipeline(
        self, repo: str, workflow: str, ref: str = "main", inputs: dict | None = None
    ) -> PipelineRun: ...

    async def get_logs(self, repo: str, run_id: str) -> str: ...

    async def cancel_run(self, repo: str, run_id: str) -> bool: ...


@runtime_checkable
class SCMProvider(Protocol):
    """Contract for source control providers (GitHub, GitLab, Azure DevOps)."""

    @property
    def name(self) -> str: ...

    async def list_repos(self, org: str | None = None) -> list[Repository]: ...

    async def get_repo(self, repo: str) -> Repository: ...

    async def create_branch(self, repo: str, branch: str, from_ref: str = "main") -> str: ...

    async def create_pull_request(
        self, repo: str, title: str, body: str, head: str, base: str = "main"
    ) -> PullRequest: ...

    async def get_pull_request(self, repo: str, pr_number: int) -> PullRequest: ...

    async def list_pull_requests(self, repo: str, state: str = "open") -> list[PullRequest]: ...


@runtime_checkable
class GitOpsProvider(Protocol):
    """Contract for GitOps providers (ArgoCD, Flux)."""

    @property
    def name(self) -> str: ...

    async def list_applications(
        self, project: str | None = None, namespace: str | None = None
    ) -> list[Application]: ...

    async def get_application(self, app_name: str) -> Application: ...

    async def sync_application(
        self, app_name: str, revision: str | None = None, prune: bool = False
    ) -> Application: ...

    async def get_sync_status(self, app_name: str) -> SyncStatus: ...

    async def rollback_application(self, app_name: str, revision_id: int) -> Application: ...

    async def get_application_history(self, app_name: str, limit: int = 10) -> list[dict]: ...
