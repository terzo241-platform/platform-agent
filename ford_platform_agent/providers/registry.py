"""Provider registry — routes requests to the correct backend.

The agent calls the registry; the registry picks the right provider
based on repo configuration or explicit provider name.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

from ford_platform_agent.config import ArgoCDConfig, GitHubConfig, TektonConfig
from ford_platform_agent.providers.argocd import ArgoCDProvider
from ford_platform_agent.providers.github import GitHubProvider
from ford_platform_agent.providers.tekton import TektonProvider

if TYPE_CHECKING:
    from ford_platform_agent.providers.base import CIProvider, GitOpsProvider, SCMProvider

logger = structlog.get_logger()


class ProviderRegistry:
    """Central registry for all provider instances.

    Lazily initializes providers on first access.
    Routes CI/CD requests to the correct provider based on repo config.
    """

    def __init__(
        self,
        github_config: GitHubConfig | None = None,
        argocd_config: ArgoCDConfig | None = None,
        tekton_config: TektonConfig | None = None,
    ) -> None:
        self._github_config = github_config or GitHubConfig()
        self._argocd_config = argocd_config or ArgoCDConfig()
        self._tekton_config = tekton_config or TektonConfig()
        self._providers: dict[str, object] = {}
        self._repo_ci_overrides: dict[str, str] = {}

    @property
    def github(self) -> GitHubProvider:
        if "github" not in self._providers:
            self._providers["github"] = GitHubProvider(self._github_config)
        return self._providers["github"]  # type: ignore[return-value]

    @property
    def argocd(self) -> ArgoCDProvider:
        if "argocd" not in self._providers:
            self._providers["argocd"] = ArgoCDProvider(self._argocd_config)
        return self._providers["argocd"]  # type: ignore[return-value]

    @property
    def tekton(self) -> TektonProvider:
        if "tekton" not in self._providers:
            self._providers["tekton"] = TektonProvider(self._tekton_config)
        return self._providers["tekton"]  # type: ignore[return-value]

    def set_repo_ci_provider(self, repo: str, provider: str) -> None:
        self._repo_ci_overrides[repo] = provider

    def get_ci(self, repo: str | None = None) -> CIProvider:
        if repo and repo in self._repo_ci_overrides:
            provider_name = self._repo_ci_overrides[repo]
            if provider_name == "tekton":
                return self.tekton
        return self.github

    def get_scm(self) -> SCMProvider:
        return self.github

    def get_gitops(self) -> GitOpsProvider:
        return self.argocd

    def list_available(self) -> dict[str, list[str]]:
        available: dict[str, list[str]] = {"ci": ["github"], "scm": ["github"], "gitops": []}
        if self._tekton_config.api_url:
            available["ci"].append("tekton")
        if self._argocd_config.server:
            available["gitops"].append("argocd")
        return available

    async def close_all(self) -> None:
        for provider in self._providers.values():
            if hasattr(provider, "close"):
                await provider.close()
