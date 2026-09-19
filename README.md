# Ford Platform Agent

AI-powered Agentic Development Platform built on [Google ADK](https://adk.dev/).

Provides developer self-service for CI/CD, source control, and GitOps through natural language or CLI, with enterprise guardrails (OWASP LLM06 mitigation).

## Architecture

```
Developer → Claude Code / Gemini CLI / ford-cli / Slack
                         │
              Google ADK Agent (Gemini Flash / Claude Sonnet via Vertex AI)
                         │
         ┌───────────────┼───────────────┐
         │               │               │
    CI/CD Tools     SCM Tools     GitOps Tools
         │               │               │
    ┌────┴────┐     GitHub API    ArgoCD API
    │         │
  GitHub   Tekton
  Actions  Pipelines
```

## Quick Start

```bash
# Configure
cp .env.example .env
# Edit .env with your GitHub token + org

# CLI
python -m ford_platform_agent providers    # check config
python -m ford_platform_agent run "list my repos"

# ADK Web UI
adk web ford_platform_agent

# ADK API Server (for Cloud Run deployment)
adk api_server ford_platform_agent
```

## Provider Abstraction

The agent doesn't know which CI/CD system is running. It calls provider-agnostic tools; the registry routes to the correct backend.

| Provider | Type | Status |
|----------|------|--------|
| GitHub Actions | CI/CD | Production |
| Tekton | CI/CD | Production |
| GitHub API | SCM | Production |
| ArgoCD | GitOps | Production |

## Guardrails (OWASP LLM06)

- **Tool Scoping**: Each tool declares allowed environments and blast radius
- **Pre-execution Validation**: Rate limiting, RBAC checks before any action
- **Human-in-the-Loop**: Production actions require confirmation (`require_confirmation`)
- **Audit Trail**: Every action logged via structlog (BigQuery in production)

## Knowledge as Code

Ford practices are stored in `knowledge/` as version-controlled YAML/Markdown. Loaded as agent context at startup — no RAG, no vector DB.

```
knowledge/
├── practices/     # Naming, deployment, cost guidelines
├── guardrails/    # Machine-readable approval rules
└── runbooks/      # Incident response procedures
```

## LLM Strategy

| Tier | Model | Use | Cost |
|------|-------|-----|------|
| Routing (90%) | Gemini 2.5 Flash | Tool selection, status queries | ~$62/mo at scale |
| Reasoning (10%) | Claude Sonnet 5 (Vertex AI) | Complex analysis, code gen | ~$26/mo at scale |

Both run through Vertex AI — VPC-SC, CMEK, IAM, audit logs from GCP infrastructure.

## Development

```bash
pip install -e ".[dev]"
pytest -v
ruff check ford_platform_agent/ tests/
```
