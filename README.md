# Ford Platform Agent

AI-powered Agentic Development Platform built on [Google ADK](https://adk.dev/).

Provides developer self-service for CI/CD, source control, and GitOps through natural language or CLI, with enterprise guardrails (OWASP LLM06 mitigation).

## Architecture

```
Developer → Claude Code / VS Code / Gemini CLI / ford-cli / Slack
                         │
                    MCP Server (stdio / HTTP)
                         │
              Google ADK Agent (Gemini Flash / Claude Sonnet via Vertex AI)
                         │
         ┌───────────┬───┴──────┬──────────────┐
         │           │          │              │
    CI/CD Tools  SCM Tools  GitOps Tools  Infra Tools
         │           │          │              │
    ┌────┴────┐  GitHub API  ArgoCD API  Terraform GitOps
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
ford-agent providers                      # check config
ford-agent run "list my repos"            # single query

# MCP Server (for Claude Code / VS Code Copilot / any MCP client)
ford-agent mcp                            # stdio transport
ford-agent mcp --transport streamable-http --port 8080  # HTTP

# ADK Web UI
adk web ford_platform_agent

# ADK API Server (for Cloud Run deployment)
adk api_server ford_platform_agent
```

## MCP Integration

The agent exposes all 17 tools as an [MCP server](https://modelcontextprotocol.io/) (spec 2026-07-28), compatible with any MCP client.

**Claude Code:**
```bash
claude mcp add ford-platform-agent -- ford-agent mcp
```

**VS Code Copilot:** Copy `.vscode/mcp.json` to your workspace, or add to global settings.

**Tool Annotations** (MCP risk vocabulary):
| Category | Tools | readOnlyHint | destructiveHint |
|----------|-------|-------------|----------------|
| Read | 10 tools (list, get, status) | true | false |
| Write-safe | 3 tools (trigger, create PR) | false | false |
| Destructive | 4 tools (cancel, sync, rollback, merge) | false | true |

MCP clients auto-approve read-only tools and show confirmation dialogs for destructive ones.

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
