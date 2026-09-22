"""Tests for scaffold tools — template generation, validation, and orchestration."""

from __future__ import annotations

import pytest

from ford_platform_agent.tools.scaffold import (
    _TEMPLATES,
    _validate_service_name,
    generate_project_files,
)

# ---------------------------------------------------------------------------
# Service name validation
# ---------------------------------------------------------------------------


class TestServiceNameValidation:
    def test_valid_names(self):
        assert _validate_service_name("my-api") is None
        assert _validate_service_name("data-pipeline") is None
        assert _validate_service_name("svc") is None
        assert _validate_service_name("a1b") is None

    def test_empty_name(self):
        assert "required" in _validate_service_name("")

    def test_too_short(self):
        assert "3-63" in _validate_service_name("ab")

    def test_too_long(self):
        assert "3-63" in _validate_service_name("a" * 64)

    def test_uppercase_rejected(self):
        assert "lowercase" in _validate_service_name("My-Api")

    def test_starts_with_number(self):
        assert "start with a letter" in _validate_service_name("1-api")

    def test_consecutive_hyphens(self):
        assert "consecutive" in _validate_service_name("my--api")

    def test_ends_with_hyphen(self):
        assert _validate_service_name("my-api-") is not None

    def test_special_chars(self):
        assert _validate_service_name("my_api") is not None


# ---------------------------------------------------------------------------
# Template definitions
# ---------------------------------------------------------------------------


class TestTemplateDefinitions:
    def test_all_three_templates_exist(self):
        assert "python-fastapi" in _TEMPLATES
        assert "node-nextjs" in _TEMPLATES
        assert "java-spring" in _TEMPLATES

    def test_templates_have_required_fields(self):
        for key, tmpl in _TEMPLATES.items():
            assert "name" in tmpl, f"{key} missing name"
            assert "description" in tmpl, f"{key} missing description"
            assert "language" in tmpl, f"{key} missing language"
            assert "framework" in tmpl, f"{key} missing framework"
            assert "ci_workflow" in tmpl, f"{key} missing ci_workflow"
            assert "default_port" in tmpl, f"{key} missing default_port"


# ---------------------------------------------------------------------------
# File generation — Python/FastAPI
# ---------------------------------------------------------------------------


class TestPythonFastapiFiles:
    def setup_method(self):
        self.files = generate_project_files("python-fastapi", "data-pipeline")

    def test_has_ci_workflow(self):
        assert ".github/workflows/ci.yml" in self.files

    def test_ci_references_platform_workflow(self):
        ci = self.files[".github/workflows/ci.yml"]
        assert "platform-workflows/.github/workflows/python-ci.yml@main" in ci

    def test_has_main_module(self):
        assert "data_pipeline/main.py" in self.files

    def test_main_has_health_endpoint(self):
        main = self.files["data_pipeline/main.py"]
        assert "/health" in main
        assert "FastAPI" in main

    def test_main_has_service_name(self):
        main = self.files["data_pipeline/main.py"]
        assert "data-pipeline" in main

    def test_has_dockerfile(self):
        assert "Dockerfile" in self.files

    def test_dockerfile_is_multistage(self):
        df = self.files["Dockerfile"]
        assert "AS builder" in df
        assert "COPY --from=builder" in df

    def test_dockerfile_exposes_8080(self):
        assert "EXPOSE 8080" in self.files["Dockerfile"]

    def test_has_requirements(self):
        reqs = self.files["requirements.txt"]
        assert "fastapi" in reqs
        assert "uvicorn" in reqs

    def test_has_tests(self):
        assert "tests/test_health.py" in self.files
        test = self.files["tests/test_health.py"]
        assert "test_health" in test
        assert "test_root" in test

    def test_has_gitignore(self):
        gi = self.files[".gitignore"]
        assert "__pycache__" in gi
        assert ".env" in gi

    def test_has_readme(self):
        readme = self.files["README.md"]
        assert "data-pipeline" in readme

    def test_module_name_underscored(self):
        assert "data_pipeline/__init__.py" in self.files


# ---------------------------------------------------------------------------
# File generation — Node/Next.js
# ---------------------------------------------------------------------------


class TestNodeNextjsFiles:
    def setup_method(self):
        self.files = generate_project_files("node-nextjs", "web-portal")

    def test_has_ci_workflow(self):
        ci = self.files[".github/workflows/ci.yml"]
        assert "node-ci.yml@main" in ci

    def test_has_package_json(self):
        pkg = self.files["package.json"]
        assert '"web-portal"' in pkg
        assert '"next"' in pkg

    def test_has_health_api(self):
        assert "src/app/api/health/route.ts" in self.files
        health = self.files["src/app/api/health/route.ts"]
        assert "status" in health

    def test_has_page(self):
        assert "src/app/page.tsx" in self.files

    def test_has_layout(self):
        assert "src/app/layout.tsx" in self.files

    def test_dockerfile_standalone(self):
        df = self.files["Dockerfile"]
        assert "standalone" in df or "server.js" in df

    def test_next_config_standalone(self):
        cfg = self.files["next.config.js"]
        assert "standalone" in cfg

    def test_has_gitignore(self):
        gi = self.files[".gitignore"]
        assert "node_modules" in gi
        assert ".next" in gi


# ---------------------------------------------------------------------------
# File generation — Java/Spring Boot
# ---------------------------------------------------------------------------


class TestJavaSpringFiles:
    def setup_method(self):
        self.files = generate_project_files("java-spring", "payment-api")

    def test_has_ci_workflow(self):
        ci = self.files[".github/workflows/ci.yml"]
        assert "java-ci.yml@main" in ci

    def test_has_pom(self):
        pom = self.files["pom.xml"]
        assert "payment-api" in pom
        assert "spring-boot-starter-web" in pom
        assert "spring-boot-starter-actuator" in pom

    def test_has_application_class(self):
        key = "src/main/java/com/ford/paymentapi/PaymentApiApplication.java"
        assert key in self.files
        app = self.files[key]
        assert "@SpringBootApplication" in app
        assert "payment-api" in app

    def test_has_test_class(self):
        key = "src/test/java/com/ford/paymentapi/PaymentApiApplicationTests.java"
        assert key in self.files

    def test_has_application_yaml(self):
        assert "src/main/resources/application.yaml" in self.files

    def test_dockerfile_uses_temurin(self):
        df = self.files["Dockerfile"]
        assert "temurin" in df
        assert "AS builder" in df

    def test_has_gitignore(self):
        gi = self.files[".gitignore"]
        assert "target/" in gi


# ---------------------------------------------------------------------------
# Invalid template
# ---------------------------------------------------------------------------


class TestInvalidTemplate:
    def test_unknown_template_raises(self):
        with pytest.raises(ValueError, match="Unknown template"):
            generate_project_files("ruby-rails", "my-app")


# ---------------------------------------------------------------------------
# list_templates tool
# ---------------------------------------------------------------------------


class TestListTemplates:
    async def test_returns_all_templates(self):
        from ford_platform_agent.tools.scaffold import list_templates

        result = await list_templates()
        assert "python-fastapi" in result["templates"]
        assert "node-nextjs" in result["templates"]
        assert "java-spring" in result["templates"]

    async def test_templates_have_includes(self):
        from ford_platform_agent.tools.scaffold import list_templates

        result = await list_templates()
        for key, tmpl in result["templates"].items():
            assert "includes" in tmpl, f"{key} missing includes"
            assert len(tmpl["includes"]) >= 4, f"{key} includes too few items"


# ---------------------------------------------------------------------------
# scaffold_project tool — validation
# ---------------------------------------------------------------------------


class TestScaffoldProjectValidation:
    async def test_rejects_bad_service_name(self):
        from ford_platform_agent.tools.scaffold import scaffold_project

        result = await scaffold_project(
            service_name="BAD NAME",
            template="python-fastapi",
            team="t",
            cost_center="CC-1",
        )
        assert result["error"] is True

    async def test_rejects_unknown_template(self):
        from ford_platform_agent.tools.scaffold import scaffold_project

        result = await scaffold_project(
            service_name="good-name",
            template="ruby-rails",
            team="t",
            cost_center="CC-1",
        )
        assert result["error"] is True
        assert "Unknown template" in result["message"]

    async def test_rejects_missing_team(self):
        from ford_platform_agent.tools.scaffold import scaffold_project

        result = await scaffold_project(
            service_name="good-name",
            template="python-fastapi",
            team="",
            cost_center="CC-1",
        )
        assert result["error"] is True
        assert "Team" in result["message"]

    async def test_rejects_missing_cost_center(self):
        from ford_platform_agent.tools.scaffold import scaffold_project

        result = await scaffold_project(
            service_name="good-name",
            template="python-fastapi",
            team="platform",
            cost_center="",
        )
        assert result["error"] is True
        assert "Cost center" in result["message"]


# ---------------------------------------------------------------------------
# scaffold_project tool — orchestration (mocked GitHub)
# ---------------------------------------------------------------------------


class TestScaffoldProjectOrchestration:
    async def test_full_scaffold_flow(self):
        from unittest.mock import AsyncMock, MagicMock

        from ford_platform_agent.providers.base import PullRequest, Repository
        from ford_platform_agent.providers.registry import ProviderRegistry
        from ford_platform_agent.tools import infra, scaffold

        mock_registry = MagicMock(spec=ProviderRegistry)
        mock_github = AsyncMock()
        mock_github.create_repo = AsyncMock(
            return_value=Repository(
                name="data-pipeline",
                full_name="terzo241-platform/data-pipeline",
                url="https://github.com/terzo241-platform/data-pipeline",
            )
        )
        mock_github.commit_files = AsyncMock(return_value="abc123def456")
        mock_github.create_branch = AsyncMock(return_value="sha123")
        mock_github._post = AsyncMock(return_value={"sha": "tree123"})
        mock_github._repo_path = lambda repo: f"/repos/terzo241-platform/{repo}"
        mock_github.create_pull_request = AsyncMock(
            return_value=PullRequest(
                number=42,
                title="Add data-pipeline (dev)",
                url="https://github.com/terzo241-platform/platform-terraform/pull/42",
            )
        )
        mock_github._client = AsyncMock()
        mock_registry.github = mock_github

        scaffold.set_registry(mock_registry)
        infra.set_registry(mock_registry)

        result = await scaffold.scaffold_project(
            service_name="data-pipeline",
            template="python-fastapi",
            team="analytics",
            cost_center="ANA-50100",
            environment="dev",
        )

        assert result["service_name"] == "data-pipeline"
        assert result["template"] == "python-fastapi"
        assert result["repo_url"] == "https://github.com/terzo241-platform/data-pipeline"
        assert result["files_created"] >= 7
        assert result["infra_pr"]["number"] == 42

        mock_github.create_repo.assert_called_once()
        mock_github.commit_files.assert_called_once()
        call_args = mock_github.commit_files.call_args
        files = call_args.kwargs.get("files") or call_args[1].get("files")
        assert ".github/workflows/ci.yml" in files
        assert "Dockerfile" in files

    async def test_scaffold_nextjs(self):
        from unittest.mock import AsyncMock, MagicMock

        from ford_platform_agent.providers.base import PullRequest, Repository
        from ford_platform_agent.providers.registry import ProviderRegistry
        from ford_platform_agent.tools import infra, scaffold

        mock_registry = MagicMock(spec=ProviderRegistry)
        mock_github = AsyncMock()
        mock_github.create_repo = AsyncMock(
            return_value=Repository(
                name="web-portal",
                full_name="terzo241-platform/web-portal",
                url="https://github.com/terzo241-platform/web-portal",
            )
        )
        mock_github.commit_files = AsyncMock(return_value="abc123")
        mock_github.create_branch = AsyncMock(return_value="sha123")
        mock_github._post = AsyncMock(return_value={"sha": "tree123"})
        mock_github._repo_path = lambda repo: f"/repos/terzo241-platform/{repo}"
        mock_github.create_pull_request = AsyncMock(
            return_value=PullRequest(
                number=10,
                title="Add web-portal (dev)",
                url="https://github.com/terzo241-platform/platform-terraform/pull/10",
            )
        )
        mock_github._client = AsyncMock()
        mock_registry.github = mock_github

        scaffold.set_registry(mock_registry)
        infra.set_registry(mock_registry)

        result = await scaffold.scaffold_project(
            service_name="web-portal",
            template="node-nextjs",
            team="marketing-web",
            cost_center="MKT-40210",
        )

        assert result["template"] == "node-nextjs"
        assert result["ci_workflow"] == "node-ci.yml"
        files = mock_github.commit_files.call_args.kwargs.get(
            "files"
        ) or mock_github.commit_files.call_args[1].get("files")
        assert "package.json" in files
        assert "src/app/api/health/route.ts" in files
