"""Provider abstraction layer.

Each provider implements a Protocol, allowing the agent to interact with
GitHub Actions, Tekton, ArgoCD, or any future CI/CD tool through the same interface.
"""

from ford_platform_agent.providers.base import (
    CIProvider,
    GitOpsProvider,
    SCMProvider,
)
from ford_platform_agent.providers.registry import ProviderRegistry

__all__ = ["CIProvider", "GitOpsProvider", "SCMProvider", "ProviderRegistry"]
