# Ford Platform Agent

AI-powered developer platform built on [Google ADK](https://google.github.io/adk-docs/) + [MCP](https://modelcontextprotocol.io/). Replaces internal developer portals with natural language — same 24 tools, every surface.

**317 tests passing** | **24 tools** | **3 provider backends** | **5 developer surfaces**

## Why This Exists

Internal developer portals have a 94% dissatisfaction rate (Gartner 2026). Backstage has <10% adoption outside Spotify. Meanwhile, MCP crossed 97M downloads with 970x growth in 6 months.

This agent replaces the portal approach: instead of building and maintaining a UI, developers interact through tools they already use — IDE, Slack, CLI. The AI handles intent; the tools handle execution; the guardrails handle safety.

## Architecture

```
        Slack       Chat API      CLI              Claude Code    VS Code
          │            │           │                     │           │
          └─────┬──────┘           │                     └─────┬─────┘
                ▼                  ▼                           ▼
          ADK Agent (Gemini)   ford-agent run           MCP Server
          server-side brain    ADK direct path          tool protocol
                │                  │                           │
                └────────┬─────────┘                           │
                         ▼                                     │
                  ┌──────────────────────────────────────────────┐
                  │              24 TOOLS                        │
                  │  Plain async Python — no ADK/MCP dependency  │
                  └──────────────────────────────────────────────┘
                         │
                  ProviderRegistry
                    │        │         │
                 GitHub   Tekton    ArgoCD
                 Actions  Pipelines
```

Two surfaces, same tools:
- **MCP path**: Client (Claude/Copilot) is the brain. Tools are dumb.
- **ADK path**: Server (Gemini via Vertex AI) is the brain. Tools are dumb.

## Quick Start

```bash
# Install
pip install -e ".[dev]"

# Configure
cp .env.example .env
# Set GITHUB_TOKEN and GITHUB_ORG

# Verify
ford-agent providers

# Use via CLI
ford-agent run "list my repos"
ford-agent run "show DORA metrics for sample-flask-app"

# Use via MCP (Claude Code)
claude mcp add ford-platform-agent -- ford-agent mcp

# Use via Slack
ford-agent slack

# Use via Chat API
ford-agent chat
```

## Tools (24)

| Module | Tools | What They Do |
|---|---|---|
| **CI/CD** (4) | list_pipeline_runs, get_pipeline_status, trigger_pipeline, cancel_pipeline | Pipeline operations across GitHub Actions + Tekton |
| **SCM** (4) | list_repositories, get_repository_info, list_pull_requests, create_pull_request | Source control operations |
| **GitOps** (5) | list_gitops_applications, get_application_status, sync_application, rollback_application, get_deployment_history | ArgoCD application management |
| **Infra** (4) | list_environments, get_plan_output, create_service_pr, approve_and_merge | Terraform golden path + separation of duties |
| **Scaffold** (2) | list_templates, scaffold_project | Zero-click service creation (Python/Node/Java) |
| **DORA Metrics** (5) | get_dora_metrics, get_team_metrics, compare_repos, get_metric_trends, get_dora_recommendations | 5 DORA metrics + AI rework rate + benchmarks |

### MCP Tool Annotations

| Category | Count | readOnlyHint | destructiveHint |
|---|---|---|---|
| Read-only | 16 tools | true | false |
| Write-safe | 4 tools (create PR, trigger, scaffold) | false | false |
| Destructive | 4 tools (cancel, sync, rollback, merge) | false | true |

## Provider Abstraction

Tools don't call GitHub directly — they go through a registry that auto-routes to the right backend:

```python
registry.get_ci("my-repo")     # → GitHub Actions or Tekton (auto-detected)
registry.get_gitops()          # → ArgoCD
registry.get_scm()             # → GitHub API
```

Adding a new backend (e.g., GitLab CI) = one new provider class. No tool code changes.

| Provider | Type | Status |
|---|---|---|
| GitHub Actions | CI/CD | Built |
| Tekton | CI/CD | Built |
| GitHub API | SCM | Built |
| ArgoCD | GitOps | Built |

## Guardrails

| Layer | What | How |
|---|---|---|
| Pre-execution | Rate limiting, RBAC | `before_agent_guardrail` callback |
| Per-tool | Confirmation for writes | `require_confirmation=True` on 8 write tools |
| Environment | Prod actions blocked without approval | `_PROD_BLOCKED_TOOLS` set |
| Audit | Every action logged | `after_agent_audit` callback → BigQuery |
| MCP | Tool risk annotations | `ToolAnnotations(readOnlyHint, destructiveHint)` |

OWASP LLM Top 10 mapping: see [docs/governance.md](docs/governance.md).

## DORA Metrics

Custom engine — no vendor dependency. Calculates from GitHub API on demand:

| Metric | Source | Benchmark (Elite) |
|---|---|---|
| Deployment Frequency | Merged PRs/day | > 1/day |
| Lead Time for Changes | PR open → merge | < 1 hour |
| Change Failure Rate | Reverts / total | < 5% |
| Mean Time to Recovery | Issue open → close | < 1 hour |
| AI Rework Rate | AI PR revision count | < 10% |

Includes team rollups, trend analysis, cross-repo comparison, and grade-based recommendations.

## LLM Strategy

| Tier | Model | Use | Cost |
|---|---|---|---|
| Routing (90%) | Gemini 2.5 Flash | Tool selection, status queries | ~$0.0002/query |
| Reasoning (10%) | Claude Sonnet 5 via Vertex AI | Complex analysis, multi-step | ~$0.017/query |
| MCP path | Client's model (Claude Opus, etc.) | IDE interactions | Client bears cost |

Blended cost at Ford scale (80 teams): ~$240/month. See [docs/cost-analysis.md](docs/cost-analysis.md).

## Project Structure

```
ford_platform_agent/
  __main__.py       (176 lines)  CLI: run, serve, mcp, chat, slack, providers
  config.py          (95 lines)  Pydantic Settings — all env-var configs
  agent.py          (139 lines)  ADK agent + Gemini brain + guardrails
  mcp_server.py     (557 lines)  MCP server — 24 tools, dual transport
  chat.py           (338 lines)  FastAPI chat API — SSE streaming, sessions
  slack_bot.py      (259 lines)  Slack bot — Bolt SDK, thread sessions
  callbacks.py      (181 lines)  Guardrail + audit callbacks
  providers/
    base.py         (145 lines)  Abstract interfaces
    registry.py      (89 lines)  Auto-routing registry
    github.py       (412 lines)  GitHub Actions + API provider
    argocd.py       (230 lines)  ArgoCD provider
    tekton.py       (197 lines)  Tekton provider
  tools/
    cicd.py         (245 lines)  4 CI/CD tools
    scm.py          (210 lines)  4 SCM tools
    gitops.py       (298 lines)  5 GitOps tools
    infra.py        (410 lines)  4 Infra tools (golden path TF)
    scaffold.py     (602 lines)  2 Scaffold tools (3 templates)
    metrics.py      (912 lines)  5 DORA metrics tools
  knowledge/                     Version-controlled practices (YAML)

tests/                (2,864 lines, 317 tests)
scripts/
  governance-setup.sh (142 lines)  GitHub org-level security automation
  policy-check.sh     (223 lines)  Pre-merge policy validation
docs/
  architecture-deep-dive.md       Dual-surface design, LLM router plan
  architecture-visual.html        Interactive 4-layer stack visualization
  cost-analysis.md                LLM + infra + 3-year TCO ($140K)
  ford-scale-guide.md             GHEC EMU, VPC-SC, migration path
  governance.md                   3-layer enforcement, OWASP mapping
  platform-proposal.html          12-slide leadership presentation
```

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest -v

# Lint
ruff check ford_platform_agent/ tests/

# Policy check (governance)
./scripts/policy-check.sh
```

## Documentation

| Doc | What |
|---|---|
| [Architecture Deep Dive](docs/architecture-deep-dive.md) | Dual-surface design, LLM router, scaling |
| [Architecture Visual](docs/architecture-visual.html) | Interactive 4-layer stack mapped to code |
| [Cost Analysis](docs/cost-analysis.md) | LLM costs, infra TCO, 3-year ROI (2,114%) |
| [Ford-Scale Guide](docs/ford-scale-guide.md) | GHEC EMU, VPC-SC, RBAC, migration path |
| [Governance](docs/governance.md) | 3-layer enforcement, OWASP LLM Top 10 |
| [Platform Proposal](docs/platform-proposal.html) | 12-slide leadership presentation |
| [POC Summary](docs/poc-summary.md) | 20-day journey, metrics, honest assessment |

## License

Internal — Ford Motor Company.
