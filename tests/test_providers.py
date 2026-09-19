"""Tests for provider abstraction layer."""

from __future__ import annotations

from ford_platform_agent.config import ArgoCDConfig, TektonConfig
from ford_platform_agent.providers.base import (
    CIProvider,
    RunStatus,
    SCMProvider,
    SyncStatus,
)
from ford_platform_agent.providers.github import GitHubProvider, _parse_run_status
from ford_platform_agent.providers.registry import ProviderRegistry


class TestRunStatusMapping:
    def test_completed_success(self):
        assert _parse_run_status("completed", "success") == RunStatus.SUCCESS

    def test_completed_failure(self):
        assert _parse_run_status("completed", "failure") == RunStatus.FAILURE

    def test_completed_cancelled(self):
        assert _parse_run_status("completed", "cancelled") == RunStatus.CANCELLED

    def test_in_progress(self):
        assert _parse_run_status("in_progress", None) == RunStatus.RUNNING

    def test_queued(self):
        assert _parse_run_status("queued", None) == RunStatus.PENDING

    def test_unknown_status(self):
        assert _parse_run_status("something_new", None) == RunStatus.UNKNOWN


class TestProviderRegistry:
    def test_default_ci_is_github(self):
        registry = ProviderRegistry()
        ci = registry.get_ci()
        assert isinstance(ci, GitHubProvider)

    def test_override_repo_to_tekton(self):
        registry = ProviderRegistry(
            tekton_config=TektonConfig(api_url="http://k8s-api:443")
        )
        registry.set_repo_ci_provider("legacy-app", "tekton")
        ci = registry.get_ci("legacy-app")
        assert ci.name == "tekton"

    def test_non_overridden_repo_uses_github(self):
        registry = ProviderRegistry()
        registry.set_repo_ci_provider("legacy-app", "tekton")
        ci = registry.get_ci("new-app")
        assert ci.name == "github"

    def test_list_available_providers(self):
        registry = ProviderRegistry(
            argocd_config=ArgoCDConfig(server="https://argocd.example.com"),
            tekton_config=TektonConfig(api_url="http://k8s-api:443"),
        )
        available = registry.list_available()
        assert "github" in available["ci"]
        assert "tekton" in available["ci"]
        assert "argocd" in available["gitops"]

    def test_list_available_without_optional(self):
        registry = ProviderRegistry()
        available = registry.list_available()
        assert available["ci"] == ["github"]
        assert available["gitops"] == []


class TestProtocolConformance:
    def test_github_is_ci_provider(self):
        provider = GitHubProvider()
        assert isinstance(provider, CIProvider)

    def test_github_is_scm_provider(self):
        provider = GitHubProvider()
        assert isinstance(provider, SCMProvider)


class TestSyncStatus:
    def test_all_statuses_have_values(self):
        assert SyncStatus.SYNCED.value == "synced"
        assert SyncStatus.OUT_OF_SYNC.value == "out_of_sync"
        assert SyncStatus.HEALTHY.value == "healthy"
        assert SyncStatus.DEGRADED.value == "degraded"
