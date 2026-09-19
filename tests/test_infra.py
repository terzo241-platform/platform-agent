"""Tests for infrastructure tools — template generation and environment defaults."""

from __future__ import annotations

from ford_platform_agent.tools.infra import (
    _ENVIRONMENT_DEFAULTS,
    _generate_tf_config,
)


class TestTerraformConfigGeneration:
    def test_generates_valid_module_block(self):
        config = _generate_tf_config(
            service_name="my-api",
            team="marketing-web",
            cost_center="MKT-40210",
            environment="dev",
        )
        assert 'module "my_api"' in config
        assert 'service_name = "my-api"' in config
        assert 'team        = "marketing-web"' in config
        assert 'cost_center = "MKT-40210"' in config
        assert 'environment  = "dev"' in config

    def test_uses_dev_defaults(self):
        config = _generate_tf_config(
            service_name="test-svc",
            team="platform",
            cost_center="ENG-10050",
            environment="dev",
        )
        assert "min_instances = 0" in config
        assert "max_instances = 3" in config
        assert 'allow_unauthenticated = true' in config
        assert "INGRESS_TRAFFIC_ALL" in config

    def test_uses_staging_defaults(self):
        config = _generate_tf_config(
            service_name="test-svc",
            team="platform",
            cost_center="ENG-10050",
            environment="staging",
        )
        assert "min_instances = 1" in config
        assert "max_instances = 5" in config
        assert 'allow_unauthenticated = false' in config
        assert "INGRESS_TRAFFIC_INTERNAL_ONLY" in config

    def test_uses_prod_defaults(self):
        config = _generate_tf_config(
            service_name="test-svc",
            team="platform",
            cost_center="ENG-10050",
            environment="prod",
        )
        assert "min_instances = 2" in config
        assert "max_instances = 20" in config
        assert 'allow_unauthenticated = false' in config
        assert "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER" in config

    def test_custom_cpu_overrides_default(self):
        config = _generate_tf_config(
            service_name="heavy-svc",
            team="data",
            cost_center="DAT-30100",
            environment="dev",
            cpu="4",
            memory="4Gi",
        )
        assert 'cpu    = "4"' in config
        assert 'memory = "4Gi"' in config

    def test_custom_port(self):
        config = _generate_tf_config(
            service_name="flask-app",
            team="web",
            cost_center="WEB-20100",
            environment="dev",
            port=5000,
        )
        assert "port   = 5000" in config

    def test_module_name_converts_hyphens_to_underscores(self):
        config = _generate_tf_config(
            service_name="my-cool-api",
            team="t",
            cost_center="CC-1234",
            environment="dev",
        )
        assert 'module "my_cool_api"' in config

    def test_source_path_correct(self):
        config = _generate_tf_config(
            service_name="svc",
            team="t",
            cost_center="CC-1234",
            environment="dev",
        )
        assert 'source = "../../modules/cloud-run-service"' in config

    def test_image_uses_service_name(self):
        config = _generate_tf_config(
            service_name="payment-api",
            team="t",
            cost_center="CC-1234",
            environment="dev",
        )
        assert "payment-api:latest" in config


class TestSeparationOfDuties:
    """PR requester and merger must be different people."""

    async def test_blocks_self_merge(self):
        from unittest.mock import AsyncMock, MagicMock

        from ford_platform_agent.providers.base import PullRequest
        from ford_platform_agent.providers.registry import ProviderRegistry
        from ford_platform_agent.tools import infra

        mock_registry = MagicMock(spec=ProviderRegistry)
        mock_github = AsyncMock()
        mock_github.get_pull_request = AsyncMock(
            return_value=PullRequest(
                number=1, title="Add svc", url="http://x", state="open", author="alice"
            )
        )
        mock_registry.github = mock_github
        infra.set_registry(mock_registry)

        result = await infra.approve_and_merge(pr_number=1, approver="alice")
        assert result["merged"] is False
        assert "Separation of duties" in result["message"]

    async def test_requires_approver(self):
        from unittest.mock import AsyncMock, MagicMock

        from ford_platform_agent.providers.base import PullRequest
        from ford_platform_agent.providers.registry import ProviderRegistry
        from ford_platform_agent.tools import infra

        mock_registry = MagicMock(spec=ProviderRegistry)
        mock_github = AsyncMock()
        mock_github.get_pull_request = AsyncMock(
            return_value=PullRequest(
                number=1, title="Add svc", url="http://x", state="open", author="alice"
            )
        )
        mock_registry.github = mock_github
        infra.set_registry(mock_registry)

        result = await infra.approve_and_merge(pr_number=1, approver="")
        assert result["merged"] is False
        assert "different team member" in result["message"]


class TestEnvironmentDefaults:
    def test_all_environments_defined(self):
        assert "dev" in _ENVIRONMENT_DEFAULTS
        assert "staging" in _ENVIRONMENT_DEFAULTS
        assert "prod" in _ENVIRONMENT_DEFAULTS

    def test_prod_has_minimum_instances(self):
        assert _ENVIRONMENT_DEFAULTS["prod"]["min_instances"] >= 2

    def test_dev_allows_scale_to_zero(self):
        assert _ENVIRONMENT_DEFAULTS["dev"]["min_instances"] == 0

    def test_prod_is_private(self):
        assert _ENVIRONMENT_DEFAULTS["prod"]["allow_unauthenticated"] == "false"
        assert "INTERNAL" in _ENVIRONMENT_DEFAULTS["prod"]["ingress"]


class TestListEnvironments:
    async def test_returns_all_envs(self):
        from ford_platform_agent.tools.infra import list_environments

        result = await list_environments()
        assert "dev" in result["environments"]
        assert "staging" in result["environments"]
        assert "prod" in result["environments"]

    async def test_prod_not_public(self):
        from ford_platform_agent.tools.infra import list_environments

        result = await list_environments()
        assert result["environments"]["prod"]["public_access"] is False

    async def test_dev_is_public(self):
        from ford_platform_agent.tools.infra import list_environments

        result = await list_environments()
        assert result["environments"]["dev"]["public_access"] is True
