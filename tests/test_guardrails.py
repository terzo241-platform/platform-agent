"""Tests for guardrail callbacks and knowledge loading."""

from __future__ import annotations

import os
import tempfile

import yaml

from ford_platform_agent.callbacks import (
    GuardrailConfig,
    RateLimiter,
    init_callbacks,
    load_guardrail_rules,
    requires_confirmation_for_env,
)
from ford_platform_agent.knowledge import get_knowledge_instruction, load_knowledge


class TestGuardrailConfig:
    def test_default_protected_environments(self):
        config = GuardrailConfig()
        assert "prod" in config.protected_environments
        assert "production" in config.protected_environments

    def test_custom_protected_environments(self):
        config = GuardrailConfig(approval_required_envs="prod,prd,live")
        assert "prod" in config.protected_environments
        assert "prd" in config.protected_environments
        assert "live" in config.protected_environments
        assert "staging" not in config.protected_environments


class TestRequiresConfirmation:
    def setup_method(self):
        init_callbacks(GuardrailConfig())

    def test_prod_requires_confirmation(self):
        assert requires_confirmation_for_env(environment="prod") is True

    def test_production_requires_confirmation(self):
        assert requires_confirmation_for_env(environment="production") is True

    def test_dev_does_not_require(self):
        assert requires_confirmation_for_env(environment="dev") is False

    def test_staging_does_not_require(self):
        assert requires_confirmation_for_env(environment="staging") is False

    def test_empty_does_not_require(self):
        assert requires_confirmation_for_env(environment="") is False

    def test_case_insensitive(self):
        assert requires_confirmation_for_env(environment="PROD") is True
        assert requires_confirmation_for_env(environment="Production") is True


class TestRateLimiter:
    def test_allows_under_limit(self):
        limiter = RateLimiter(max_per_min=5)
        for _ in range(5):
            assert limiter.check() is True

    def test_blocks_over_limit(self):
        limiter = RateLimiter(max_per_min=2)
        assert limiter.check() is True
        assert limiter.check() is True
        assert limiter.check() is False


class TestKnowledgeLoading:
    def test_load_from_nonexistent_dir(self):
        result = load_knowledge("/nonexistent/path")
        assert result == ""

    def test_load_yaml_practices(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            practices_dir = os.path.join(tmpdir, "practices")
            os.makedirs(practices_dir)
            with open(os.path.join(practices_dir, "test.yaml"), "w") as f:
                yaml.dump({"rule": "always test"}, f)
            result = load_knowledge(tmpdir)
            assert "always test" in result

    def test_load_markdown_runbook(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runbooks_dir = os.path.join(tmpdir, "runbooks")
            os.makedirs(runbooks_dir)
            with open(os.path.join(runbooks_dir, "test.md"), "w") as f:
                f.write("# Test Runbook\nStep 1: Do the thing")
            result = load_knowledge(tmpdir)
            assert "Test Runbook" in result

    def test_knowledge_instruction_wrapping(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            practices_dir = os.path.join(tmpdir, "practices")
            os.makedirs(practices_dir)
            with open(os.path.join(practices_dir, "test.yaml"), "w") as f:
                yaml.dump({"rule": "test"}, f)
            result = get_knowledge_instruction(tmpdir)
            assert "Ford Platform Knowledge Base" in result


class TestGuardrailRulesLoading:
    def test_load_rules_from_yaml(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "env-rules.yaml"), "w") as f:
                yaml.dump({"rules": [{"name": "prod-block", "environments": ["prod"]}]}, f)
            rules = load_guardrail_rules(tmpdir)
            assert "env-rules" in rules
            assert rules["env-rules"]["rules"][0]["name"] == "prod-block"
