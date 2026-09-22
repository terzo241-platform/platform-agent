"""Scaffold tools — zero-click project setup via the AI agent.

The IDP replacement: developer says "I need a new Python service" and the agent
creates the repo, pushes scaffolded code + CI workflow, and opens a Terraform PR
for Cloud Run infrastructure — all in one action.
"""

from __future__ import annotations

import re
import textwrap
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from ford_platform_agent.providers.registry import ProviderRegistry

logger = structlog.get_logger()

_registry: ProviderRegistry | None = None

TERRAFORM_REPO = "platform-terraform"
WORKFLOWS_ORG = "terzo241-platform"


def set_registry(registry: ProviderRegistry) -> None:
    global _registry
    _registry = registry


def _get_registry() -> ProviderRegistry:
    if _registry is None:
        raise RuntimeError("ProviderRegistry not initialized.")
    return _registry


# ---------------------------------------------------------------------------
# Project templates — golden paths for Ford's 3 primary tech stacks
# ---------------------------------------------------------------------------

_TEMPLATES: dict[str, dict] = {
    "python-fastapi": {
        "name": "Python FastAPI",
        "description": "Production-ready Python API service with FastAPI and pytest.",
        "language": "Python",
        "framework": "FastAPI",
        "ci_workflow": "python-ci.yml",
        "default_port": 8080,
    },
    "node-nextjs": {
        "name": "Node.js Next.js",
        "description": "Server-rendered React app with Next.js, health API, and Playwright-ready.",
        "language": "JavaScript",
        "framework": "Next.js",
        "ci_workflow": "node-ci.yml",
        "default_port": 3000,
    },
    "java-spring": {
        "name": "Java Spring Boot",
        "description": "Enterprise Java API with Spring Boot 3, health actuator, and Maven.",
        "language": "Java",
        "framework": "Spring Boot",
        "ci_workflow": "java-ci.yml",
        "default_port": 8080,
    },
}


def _validate_service_name(name: str) -> str | None:
    if not name:
        return "Service name is required."
    if len(name) < 3 or len(name) > 63:
        return "Service name must be 3-63 characters."
    if not re.match(r"^[a-z][a-z0-9-]*[a-z0-9]$", name):
        return (
            "Service name must be lowercase, start with a letter, "
            "use only letters/digits/hyphens."
        )
    if "--" in name:
        return "Service name cannot contain consecutive hyphens."
    return None


# ---------------------------------------------------------------------------
# File generators per template
# ---------------------------------------------------------------------------


def _ci_workflow(template_key: str, service_name: str) -> str:
    tmpl = _TEMPLATES[template_key]
    workflow_file = tmpl["ci_workflow"]

    extras = ""
    if template_key == "python-fastapi":
        extras = "\n      python-version: '3.12'\n      enable-security-scan: true"
    elif template_key == "node-nextjs":
        extras = "\n      node-version: '22'\n      enable-playwright: false"
    elif template_key == "java-spring":
        extras = "\n      java-version: '21'\n      enable-security-scan: true"

    return textwrap.dedent(f"""\
        name: CI
        on:
          push:
            branches: [main]
          pull_request:
            branches: [main]
        jobs:
          ci:
            uses: {WORKFLOWS_ORG}/platform-workflows/.github/workflows/{workflow_file}@main
            with:{extras}
    """)


def _python_fastapi_files(service_name: str) -> dict[str, str]:
    module = service_name.replace("-", "_")
    return {
        f"{module}/__init__.py": "",
        f"{module}/main.py": textwrap.dedent(f"""\
            from fastapi import FastAPI

            app = FastAPI(title="{service_name}")


            @app.get("/")
            async def root():
                return {{"service": "{service_name}", "status": "running"}}


            @app.get("/health")
            async def health():
                return {{"status": "ok"}}
        """),
        "requirements.txt": (
            "fastapi>=0.115.0\nuvicorn[standard]>=0.32.0\n"
            "pytest>=8.0\nhttpx>=0.27.0\n"
        ),
        "tests/__init__.py": "",
        "tests/test_health.py": textwrap.dedent(f"""\
            from fastapi.testclient import TestClient

            from {module}.main import app

            client = TestClient(app)


            def test_root():
                resp = client.get("/")
                assert resp.status_code == 200
                assert resp.json()["service"] == "{service_name}"


            def test_health():
                resp = client.get("/health")
                assert resp.status_code == 200
                assert resp.json()["status"] == "ok"
        """),
        "Dockerfile": textwrap.dedent(f"""\
            FROM python:3.12-slim AS builder
            WORKDIR /app
            COPY requirements.txt .
            RUN pip install --no-cache-dir -r requirements.txt

            FROM python:3.12-slim
            WORKDIR /app
            COPY --from=builder /usr/local/lib/python3.12/site-packages \
                /usr/local/lib/python3.12/site-packages
            COPY --from=builder /usr/local/bin/uvicorn /usr/local/bin/uvicorn
            COPY . .
            EXPOSE 8080
            CMD ["uvicorn", "{module}.main:app", "--host", "0.0.0.0", "--port", "8080"]
        """),
        ".gitignore": textwrap.dedent("""\
            __pycache__/
            *.pyc
            .venv/
            .env
            .pytest_cache/
            dist/
            *.egg-info/
            .ruff_cache/
        """),
        "README.md": textwrap.dedent(f"""\
            # {service_name}

            Python FastAPI service scaffolded by Ford Platform Agent.

            ## Quick start

            ```bash
            pip install -r requirements.txt
            uvicorn {module}.main:app --reload
            ```

            ## Tests

            ```bash
            pytest
            ```
        """),
    }


def _node_nextjs_files(service_name: str) -> dict[str, str]:
    return {
        "package.json": textwrap.dedent(f"""\
            {{
              "name": "{service_name}",
              "version": "0.1.0",
              "private": true,
              "scripts": {{
                "dev": "next dev",
                "build": "next build",
                "start": "next start",
                "test": "echo \\"no unit tests yet\\" && exit 0"
              }},
              "dependencies": {{
                "next": "^16.0.0",
                "react": "^19.0.0",
                "react-dom": "^19.0.0"
              }}
            }}
        """),
        "next.config.js": textwrap.dedent("""\
            /** @type {import('next').NextConfig} */
            const nextConfig = { output: 'standalone' }
            module.exports = nextConfig
        """),
        "src/app/page.tsx": textwrap.dedent(f"""\
            export default function Home() {{
              return <h1>{service_name}</h1>
            }}
        """),
        "src/app/layout.tsx": textwrap.dedent(f"""\
            export const metadata = {{ title: '{service_name}' }}

            export default function RootLayout({{ children }}: {{ children: React.ReactNode }}) {{
              return (
                <html lang="en">
                  <body>{{children}}</body>
                </html>
              )
            }}
        """),
        "src/app/api/health/route.ts": textwrap.dedent("""\
            import { NextResponse } from 'next/server'

            export async function GET() {
              return NextResponse.json({ status: 'ok' })
            }
        """),
        "Dockerfile": textwrap.dedent("""\
            FROM node:22-alpine AS deps
            WORKDIR /app
            COPY package.json package-lock.json* ./
            RUN npm ci --ignore-scripts

            FROM node:22-alpine AS builder
            WORKDIR /app
            COPY --from=deps /app/node_modules ./node_modules
            COPY . .
            RUN npm run build

            FROM node:22-alpine
            WORKDIR /app
            COPY --from=builder /app/.next/standalone ./
            COPY --from=builder /app/.next/static ./.next/static
            EXPOSE 3000
            CMD ["node", "server.js"]
        """),
        ".gitignore": textwrap.dedent("""\
            node_modules/
            .next/
            out/
            .env
            .env.local
            npm-debug.log*
        """),
        "README.md": textwrap.dedent(f"""\
            # {service_name}

            Next.js app scaffolded by Ford Platform Agent.

            ## Quick start

            ```bash
            npm install
            npm run dev
            ```
        """),
    }


def _java_spring_files(service_name: str) -> dict[str, str]:
    class_name = "".join(w.capitalize() for w in service_name.split("-"))
    package_path = service_name.replace("-", "")
    return {
        "pom.xml": textwrap.dedent(f"""\
            <?xml version="1.0" encoding="UTF-8"?>
            <project xmlns="http://maven.apache.org/POM/4.0.0"
                     xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                     xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
              <modelVersion>4.0.0</modelVersion>
              <parent>
                <groupId>org.springframework.boot</groupId>
                <artifactId>spring-boot-starter-parent</artifactId>
                <version>3.4.0</version>
              </parent>
              <groupId>com.ford</groupId>
              <artifactId>{service_name}</artifactId>
              <version>0.1.0</version>
              <dependencies>
                <dependency>
                  <groupId>org.springframework.boot</groupId>
                  <artifactId>spring-boot-starter-web</artifactId>
                </dependency>
                <dependency>
                  <groupId>org.springframework.boot</groupId>
                  <artifactId>spring-boot-starter-actuator</artifactId>
                </dependency>
                <dependency>
                  <groupId>org.springframework.boot</groupId>
                  <artifactId>spring-boot-starter-test</artifactId>
                  <scope>test</scope>
                </dependency>
              </dependencies>
              <build>
                <plugins>
                  <plugin>
                    <groupId>org.springframework.boot</groupId>
                    <artifactId>spring-boot-maven-plugin</artifactId>
                  </plugin>
                </plugins>
              </build>
            </project>
        """),
        f"src/main/java/com/ford/{package_path}/{class_name}Application.java": textwrap.dedent(f"""\
            package com.ford.{package_path};

            import org.springframework.boot.SpringApplication;
            import org.springframework.boot.autoconfigure.SpringBootApplication;
            import org.springframework.web.bind.annotation.GetMapping;
            import org.springframework.web.bind.annotation.RestController;
            import java.util.Map;

            @SpringBootApplication
            @RestController
            public class {class_name}Application {{

                public static void main(String[] args) {{
                    SpringApplication.run({class_name}Application.class, args);
                }}

                @GetMapping("/")
                public Map<String, String> root() {{
                    return Map.of("service", "{service_name}", "status", "running");
                }}
            }}
        """),
        "src/main/resources/application.yaml": textwrap.dedent("""\
            server:
              port: 8080
            management:
              endpoints:
                web:
                  exposure:
                    include: health,info
        """),
        f"src/test/java/com/ford/{package_path}/"
        f"{class_name}ApplicationTests.java": textwrap.dedent(f"""\
            package com.ford.{package_path};

            import org.junit.jupiter.api.Test;
            import org.springframework.boot.test.context.SpringBootTest;

            @SpringBootTest
            class {class_name}ApplicationTests {{
                @Test
                void contextLoads() {{
                }}
            }}
        """),
        "Dockerfile": textwrap.dedent("""\
            FROM eclipse-temurin:21-jdk-alpine AS builder
            WORKDIR /app
            COPY pom.xml .
            COPY src ./src
            RUN apk add --no-cache maven && mvn package -DskipTests

            FROM eclipse-temurin:21-jre-alpine
            WORKDIR /app
            COPY --from=builder /app/target/*.jar app.jar
            EXPOSE 8080
            ENTRYPOINT ["java", "-jar", "app.jar"]
        """),
        ".gitignore": textwrap.dedent("""\
            target/
            .idea/
            *.iml
            .settings/
            .classpath
            .project
            *.class
            .env
        """),
        "README.md": textwrap.dedent(f"""\
            # {service_name}

            Spring Boot service scaffolded by Ford Platform Agent.

            ## Quick start

            ```bash
            mvn spring-boot:run
            ```

            ## Tests

            ```bash
            mvn test
            ```
        """),
    }


_FILE_GENERATORS = {
    "python-fastapi": _python_fastapi_files,
    "node-nextjs": _node_nextjs_files,
    "java-spring": _java_spring_files,
}


def generate_project_files(template: str, service_name: str) -> dict[str, str]:
    """Generate all project files for a given template."""
    generator = _FILE_GENERATORS.get(template)
    if not generator:
        raise ValueError(f"Unknown template: {template}")

    files = generator(service_name)
    files[".github/workflows/ci.yml"] = _ci_workflow(template, service_name)
    return files


# ---------------------------------------------------------------------------
# Agent-facing tools
# ---------------------------------------------------------------------------


async def list_templates() -> dict:
    """List available project templates (golden paths).

    Shows the supported project archetypes with their language, framework,
    and what gets generated. Use this to help a developer pick the right
    starting point for a new service.

    Returns:
        Available templates with descriptions and included tooling.
    """
    return {
        "templates": {
            key: {
                "name": t["name"],
                "description": t["description"],
                "language": t["language"],
                "framework": t["framework"],
                "default_port": t["default_port"],
                "includes": [
                    "CI workflow (centralized, pre-configured)",
                    "Multi-stage Dockerfile (production-ready)",
                    "Health check endpoint",
                    "Unit test scaffold",
                    ".gitignore",
                    "README with quickstart",
                ],
            }
            for key, t in _TEMPLATES.items()
        },
        "message": (
            "Pick a template that matches the team's tech stack. "
            "Each includes CI, Docker, health checks, and tests out of the box."
        ),
    }


async def scaffold_project(
    service_name: str,
    template: str,
    team: str,
    cost_center: str,
    description: str = "",
    environment: str = "dev",
    private: bool = False,
) -> dict:
    """Create a new project from scratch — repo, code, CI, and infrastructure.

    This is the zero-click project setup. It:
    1. Creates a new GitHub repository in the org
    2. Pushes scaffolded project files (app code, Dockerfile, tests)
    3. Adds a CI workflow referencing the centralized platform workflows
    4. Opens a Terraform PR in platform-terraform for Cloud Run infrastructure

    This is a WRITE operation that creates a repository and opens a PR.

    Args:
        service_name: Name for the new service (lowercase, hyphens, 3-63 chars).
        template: Project template — 'python-fastapi', 'node-nextjs', or 'java-spring'.
        team: Owning team name (e.g., 'marketing-web').
        cost_center: Finance cost center (e.g., 'MKT-40210').
        description: Optional repo description.
        environment: Initial environment to provision — 'dev', 'staging', or 'prod'.
        private: Whether the repo should be private (default: public).

    Returns:
        Links to the created repo and infrastructure PR.
    """
    validation_error = _validate_service_name(service_name)
    if validation_error:
        return {"error": True, "message": validation_error}

    if template not in _TEMPLATES:
        return {
            "error": True,
            "message": (
                f"Unknown template '{template}'. "
                f"Available: {', '.join(_TEMPLATES.keys())}. "
                f"Use list_templates() to see details."
            ),
        }

    if not team:
        return {"error": True, "message": "Team name is required."}
    if not cost_center:
        return {"error": True, "message": "Cost center is required."}

    reg = _get_registry()
    github = reg.github
    tmpl = _TEMPLATES[template]

    repo_desc = description or f"{tmpl['name']} service — {team}"
    repo = await github.create_repo(
        name=service_name,
        description=repo_desc,
        private=private,
        auto_init=True,
    )

    project_files = generate_project_files(template, service_name)
    commit_sha = await github.commit_files(
        repo=service_name,
        branch="main",
        message=(
            f"Scaffold {service_name} from {template} template\n\n"
            f"Generated by Ford Platform Agent.\n"
            f"Team: {team}, Cost Center: {cost_center}"
        ),
        files=project_files,
    )

    from ford_platform_agent.tools.infra import create_service_pr

    infra_result = await create_service_pr(
        service_name=service_name,
        team=team,
        cost_center=cost_center,
        environment=environment,
        port=tmpl["default_port"],
    )

    logger.info(
        "project_scaffolded",
        service=service_name,
        template=template,
        team=team,
        environment=environment,
        files=len(project_files),
    )

    return {
        "service_name": service_name,
        "template": template,
        "repo_url": repo.url,
        "repo_full_name": repo.full_name,
        "commit_sha": commit_sha[:8],
        "files_created": len(project_files),
        "ci_workflow": tmpl["ci_workflow"],
        "infra_pr": {
            "number": infra_result.get("pr_number"),
            "url": infra_result.get("pr_url"),
            "environment": environment,
        },
        "message": (
            f"Project '{service_name}' created from {tmpl['name']} template.\n"
            f"- Repo: {repo.url}\n"
            f"- {len(project_files)} files pushed (app, Dockerfile, CI, tests)\n"
            f"- CI workflow calls {WORKFLOWS_ORG}/platform-workflows/{tmpl['ci_workflow']}\n"
            f"- Infra PR #{infra_result.get('pr_number')} opened in {TERRAFORM_REPO} "
            f"for {environment} Cloud Run deployment\n"
            f"- Next: check the PR for Terraform plan, then merge to provision infra"
        ),
    }
