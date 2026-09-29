# Ford Platform Agent — Architecture Deep Dive

## Dual-Surface Architecture

The platform agent exposes the **same 24 tools** through two independent surfaces.
This is not redundancy — each surface serves a different audience.

```
                   Slack    Chat API    CLI
                     │         │         │
                     └────┬────┘         │
                          ▼              │
                    ADK Agent (Gemini)   │    ← server-side brain
                          │              │       for non-MCP surfaces
                          │              │
                     ┌────┴──────────────┘
                     ▼
              ┌──────────────┐
              │  24 TOOLS    │  ← one codebase, plain async Python
              └──────────────┘
                     ▲
                     │
                MCP Server               ← protocol adapter
                     ▲                      for MCP-compatible clients
                     │
              ┌──────┴──────┐
              │             │
          Claude Code    VS Code         ← client-side brain
```

### Why two surfaces?

| Question | MCP Path | ADK Path |
|---|---|---|
| Who is the LLM brain? | Client (Claude, Copilot) | Server (Gemini via Vertex AI) |
| Where do guardrails run? | Client-side (trust the client) | Server-side (`before_agent_callback`) |
| Who are the users? | Developers in IDEs | Slack users, Chat API consumers, CLI users |
| Needs GCP/Gemini? | No | Yes |
| Entry point | `ford-agent mcp` | `ford-agent run/chat/slack` |

MCP alone can't serve Slack or Chat API — those surfaces have no MCP client.
ADK alone limits you to Gemini users — misses the IDE-native developer experience.
Together they cover every surface.

---

## Tool Layer (The Kitchen)

All 24 tools are plain Python async functions in `ford_platform_agent/tools/`.
They know nothing about ADK or MCP — they just call APIs and return dicts.

```
tools/
  cicd.py      →  4 tools  (list_pipeline_runs, get_pipeline_status, trigger_pipeline, cancel_pipeline)
  scm.py       →  4 tools  (list_repositories, get_repository_info, list_pull_requests, create_pull_request)
  gitops.py    →  5 tools  (list_gitops_applications, get_application_status, sync_application, rollback_application, get_deployment_history)
  infra.py     →  4 tools  (list_environments, get_plan_output, create_service_pr, approve_and_merge)
  scaffold.py  →  2 tools  (list_templates, scaffold_project)
  metrics.py   →  5 tools  (get_dora_metrics, get_team_metrics, compare_repos, get_metric_trends, get_dora_recommendations)
                ────────
                24 total
```

Each tool is wrapped twice:
- **MCP wrapper** (`mcp_server.py`): `@mcp.tool(name="...", annotations=...)` — adds MCP metadata and transport
- **ADK wrapper** (`agent.py`): `FunctionTool(func, require_confirmation=...)` — adds ADK guardrails

### Provider Abstraction

Tools don't call GitHub directly. They go through the `ProviderRegistry`:

```
Tool function
  → ProviderRegistry.get_ci(repo)    ← auto-detects: GitHub Actions or Tekton?
  → ProviderRegistry.get_gitops()    ← returns: ArgoCD provider
  → ProviderRegistry.get_scm()       ← returns: GitHub provider

Adding a new backend (e.g., GitLab CI) = one new provider class + registry entry.
No tool code changes.
```

---

## LLM Router (Planned, Not Yet Implemented)

### Current state

```python
# config.py
class AgentConfig:
    model: str = "gemini-2.5-flash"           # ← used by ADK agent
    reasoning_model: str = "claude-sonnet-5"   # ← defined but NEVER referenced in agent.py
```

Today: Gemini Flash handles ALL queries in ADK path. No routing.
In MCP path: Claude Opus handles everything (routing is irrelevant — it's already the best model).

### Target architecture

```
User prompt arrives
       │
       ▼
┌─────────────────────────────────┐
│  COMPLEXITY CLASSIFIER          │
│                                 │
│  Phase 1: Rule-based (free)     │
│    "list", "show", "status"     │
│       → simple → Gemini Flash   │
│    "analyze", "compare", "why"  │
│       → complex → Claude Sonnet │
│                                 │
│  Phase 2: Gemini Flash          │
│    classifies first (1 cheap    │
│    call to decide the route)    │
│                                 │
│  Phase 3: Try cheap first,      │
│    escalate if confidence < 0.7 │
└─────────────────────────────────┘
       │
  ┌────┴─────┐
  ▼          ▼
Gemini     Claude Sonnet
Flash      via Vertex AI
─────      ───────────────
$0.15/M    $3.00/M input
~200ms     ~800ms
90% of     10% of queries
queries
```

### Cost projection

```
Average cost per developer interaction: ~$0.005
  90% queries × Gemini Flash ($0.15/M)   = $0.001
  10% queries × Claude Sonnet ($3.00/M)  = $0.004

At 80 teams × 20 queries/day = 1,600 queries/day
  Monthly LLM cost: ~$240/month
```

### When routing matters — examples

```
SIMPLE (Gemini Flash):
  "list my repos"                          → call list_repositories → format response
  "what's the pipeline status?"            → call get_pipeline_status → format response
  "create a Python service called foo"     → call scaffold_project → format response

COMPLEX (Claude Sonnet):
  "Our team's velocity feels slower.       → call get_team_metrics (3 repos, 90 days)
   Analyze DORA metrics for all repos,     → call get_metric_trends (×3 repos)
   identify root cause, and propose a      → call get_dora_recommendations
   30-day improvement plan"                → synthesize multi-tool results into analysis
```

---

## Parallelism & Scaling

### MCP Path (Claude Code)

| Layer | Parallelism | Limit |
|---|---|---|
| Claude Code | Multiple MCP tools per turn (3-5 parallel) | Model decides |
| MCP Server (stdio) | Single connection, async concurrent execution | One session = one process |
| MCP Server (HTTP) | Multiple concurrent clients | Horizontally scalable |
| Tool functions | All `async def` — non-blocking I/O | asyncio event loop |
| GitHub API | Concurrent HTTP requests via httpx | 5,000 requests/hour per PAT |

### ADK Path (Gemini)

Same tool-level parallelism, plus:
- `before_agent_callback` — guardrail check before each tool call
- `after_agent_callback` — audit log after each tool call
- `require_confirmation=True` — blocks on human approval for write operations

### Scaling pattern

```
Single user (demo):
  1 Claude Code → 1 MCP (stdio) → async tools → GitHub API

Multi-user (production):
  N clients → MCP (HTTP, streamable) → async tools → GitHub API
  Stateless server → easy horizontal scaling via K8s replicas
  GitHub API rate limit (5,000/hr) is the real ceiling

  ADK agent for Slack/Chat:
  N Slack users → 1 Chat API server → ADK agent → async tools → GitHub API
  Session persistence via PostgreSQL/Firestore
```

---

## Data Layer Strategy

### What needs persistent storage (production only)

| Purpose | Solution | Why |
|---|---|---|
| Session persistence | PostgreSQL or Firestore | In-memory sessions die on pod restart |
| Audit logging | BigQuery | Compliance: every tool invocation logged with identity, args, outcome |
| OpenTelemetry traces | Cloud Trace | ADK 1.17.0+ has native OTel; flip it on |

### What stays stateless (by design)

- Tool logic — queries authoritative APIs (GitHub, ArgoCD) at call time
- DORA metrics — calculated from GitHub API on demand
- Templates/environments — hardcoded golden paths
- Provider routing — determined by config, not state

Agent logic is stateless by design — it queries authoritative APIs, not stale copies.
Session and audit persistence are infrastructure concerns, not agent concerns.

---

## Audit & Observability Roadmap

### OWASP LLM Top 10 (2025) requirements for enterprise AI agents

1. **Identity attribution** — who triggered each action
2. **Tool invocation logging** — tool name, args, outcome, latency
3. **Authorization context** — which guardrail approved/blocked
4. **Lineage tracking** — full chain from user request to tool call
5. **Integrity verification** — tamper-proof audit logs

### Staged implementation

| Phase | Scope | Implementation |
|---|---|---|
| POC (current) | BigQuery audit table | identity, tool, args, outcome, timestamp |
| Pilot | OpenTelemetry traces | ADK 1.17.0+ native OTel → Cloud Trace |
| Production | Immutable audit sink | Cloud Logging → Cloud Storage, 90-day retention, dashboards |

---

## File Map

```
ford_platform_agent/
  __main__.py        ← CLI: routes to mcp/chat/slack/run
  config.py          ← all env-var configs (Pydantic Settings)
  agent.py           ← ADK agent (Gemini brain + guardrails)
  mcp_server.py      ← MCP server (tool protocol, no brain)
  chat.py            ← FastAPI chat API (SSE streaming)
  slack_bot.py       ← Slack bot (Bolt SDK adapter)
  callbacks.py       ← guardrail + audit callbacks for ADK
  knowledge/         ← knowledge-as-code YAML files
  providers/
    base.py          ← abstract interfaces (CIProvider, SCMProvider, etc.)
    registry.py      ← auto-routing registry
    github.py        ← GitHub Actions + GitHub API provider
    argocd.py        ← ArgoCD provider
    tekton.py        ← Tekton provider
  tools/
    cicd.py          ← 4 CI/CD tools
    scm.py           ← 4 SCM tools
    gitops.py        ← 5 GitOps tools
    infra.py         ← 4 Infra tools
    scaffold.py      ← 2 Scaffold tools
    metrics.py       ← 5 DORA metrics tools
```
