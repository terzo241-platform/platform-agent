"""Configuration via environment variables with Pydantic Settings."""

from __future__ import annotations

from pydantic import Field
from pydantic_settings import BaseSettings


class AgentConfig(BaseSettings):
    model_config = {"env_prefix": "FORD_AGENT_", "env_file": ".env", "extra": "ignore"}

    model: str = "gemini-2.5-flash"
    reasoning_model: str = "claude-sonnet-5"
    app_name: str = "ford-platform-agent"


class GitHubConfig(BaseSettings):
    model_config = {"env_prefix": "GITHUB_", "env_file": ".env", "extra": "ignore"}

    token: str = ""
    org: str = ""
    api_url: str = "https://api.github.com"


class ArgoCDConfig(BaseSettings):
    model_config = {"env_prefix": "ARGOCD_", "env_file": ".env", "extra": "ignore"}

    server: str = ""
    token: str = ""
    use_kube_auth: bool = False
    insecure: bool = False


class TektonConfig(BaseSettings):
    model_config = {"env_prefix": "TEKTON_", "env_file": ".env", "extra": "ignore"}

    api_url: str = ""
    namespace: str = "tekton-pipelines"


class GuardrailConfig(BaseSettings):
    model_config = {"env_prefix": "FORD_", "env_file": ".env", "extra": "ignore"}

    approval_required_envs: str = "prod,production"
    max_concurrent_deploys: int = 3
    rate_limit_per_min: int = 30

    @property
    def protected_environments(self) -> set[str]:
        return {e.strip().lower() for e in self.approval_required_envs.split(",")}


class AuditConfig(BaseSettings):
    model_config = {"env_prefix": "FORD_AUDIT_", "env_file": ".env", "extra": "ignore"}

    dataset: str = "platform_agent_audit"
    table: str = "actions"
    project: str = Field(default="", alias="GOOGLE_CLOUD_PROJECT")


def load_all() -> dict:
    return {
        "agent": AgentConfig(),
        "github": GitHubConfig(),
        "argocd": ArgoCDConfig(),
        "tekton": TektonConfig(),
        "guardrails": GuardrailConfig(),
        "audit": AuditConfig(),
    }
