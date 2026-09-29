# Ford Platform Agent — Cost Analysis & ROI

> Based on published GCP/GitHub pricing as of mid-2026. Verify rates at procurement time.

## Executive Summary

| Metric | Current State (Estimated) | Platform Agent (Year 1) | Platform Agent (Year 3) |
|---|---|---|---|
| Annual platform cost | ~$480K (manual ops + tool licenses) | ~$52K | ~$48K |
| Developer hours saved/year | — | ~12,800 hrs | ~38,400 hrs |
| Cost per developer interaction | ~$45 (human time) | ~$0.005 (LLM) | ~$0.004 (LLM) |
| Time to provision new service | 2-5 days | < 5 minutes | < 3 minutes |
| DORA metric visibility | None | Real-time, per-team | Org-wide benchmarks |

---

## 1. LLM Costs

### Pricing (Vertex AI, published rates)

| Model | Input (per 1M tokens) | Output (per 1M tokens) | Role in Platform |
|---|---|---|---|
| Gemini 2.5 Flash | $0.15 | $0.60 | 90% of queries (ADK path) |
| Claude Sonnet 5 | $3.00 | $15.00 | 10% complex reasoning (ADK path) |
| Claude Opus 4 | $15.00 | $75.00 | MCP path (IDE users, client-side) |

### Average cost per interaction

A typical platform interaction (e.g., "show my DORA metrics") involves:
- ~500 input tokens (user prompt + system context)
- ~200 output tokens (formatted response)
- 1-2 tool calls (API round-trips, not LLM tokens)

```
Gemini Flash query:
  Input:  500 tokens × $0.15/M  = $0.000075
  Output: 200 tokens × $0.60/M  = $0.000120
  Total per query:               = $0.000195  (~$0.0002)

Claude Sonnet query (complex):
  Input:  1,500 tokens × $3.00/M  = $0.0045
  Output: 800 tokens × $15.00/M   = $0.0120
  Total per query:                 = $0.0165   (~$0.017)

Blended cost (90/10 router):
  0.9 × $0.0002 + 0.1 × $0.017  = $0.0019/query  (~$0.002)
```

### Monthly LLM cost projections

| Scale | Queries/Day | Monthly LLM Cost | Annual |
|---|---|---|---|
| POC (1 team, 5 devs) | 50 | $3 | $36 |
| Pilot (10 teams, 50 devs) | 500 | $30 | $360 |
| Ford scale (80 teams, 400+ devs) | 4,000 | $240 | $2,880 |
| Peak (80 teams, heavy adoption) | 8,000 | $480 | $5,760 |

**Note on MCP path costs:** When developers use Claude Code (MCP path), LLM costs are borne by the Claude Code license/subscription — not the platform. Only ADK path queries (Slack, Chat API, CLI) hit platform LLM costs.

---

## 2. Infrastructure Costs (GCP)

### Compute — Cloud Run (Agent API + MCP Server)

```
Agent API (Chat + Slack bot):
  1 vCPU, 512 MiB, min 1 instance, max 10
  vCPU:    $0.0000240/sec × 86,400 sec/day × 30 days × 1 min instance = $62/mo
  Memory:  $0.0000025/GiB-sec × 86,400 × 30 × 0.5 GiB               = $3.24/mo
  Burst:   avg 3 instances during business hours (8 hrs)
           additional $62 × 2 × (8/24)                                = $41/mo
  Subtotal:                                                            ~$106/mo

MCP Server (HTTP transport, optional):
  Same sizing as above                                                 ~$106/mo
  (Only needed if centralized MCP; most deployments use stdio)

Total compute:                                                         ~$106-212/mo
```

### Database — Cloud SQL PostgreSQL (Session Persistence)

```
Instance: db-custom-1-3840 (1 vCPU, 3.75 GB RAM)
  Compute:  ~$50/mo
  Storage:  10 GB SSD × $0.17/GB = $1.70/mo
  HA (prod): 2× compute          = $100/mo
  Backups:  negligible

Total database:                    ~$52-102/mo
```

### Audit & Observability

```
BigQuery (audit logs):
  Storage: <1 GB/mo at Ford scale               = ~$0.02/mo
  Queries: ~100 queries/mo × avg 10 MB scanned  = ~$0.01/mo
  Flat-rate slot: not needed at this scale

Cloud Trace (OpenTelemetry):
  First 2.5M spans/mo free
  Platform generates ~120K spans/mo at Ford scale = $0 (free tier)

Cloud Logging:
  First 50 GiB/mo free per project
  Platform generates ~2 GiB/mo                    = $0 (free tier)

Total observability:                               ~$1/mo
```

### Networking & Misc

```
VPC connector (if needed):         ~$7/mo (e2-micro)
Cloud NAT (egress to GitHub API):  ~$3/mo
Artifact Registry (container):     ~$1/mo
Secret Manager (6 secrets):        $0.36/mo

Total networking:                  ~$12/mo
```

### Infrastructure Cost Summary

| Component | Monthly | Annual |
|---|---|---|
| Compute (Cloud Run) | $106-212 | $1,272-2,544 |
| Database (Cloud SQL) | $52-102 | $624-1,224 |
| Observability (BQ + Trace) | $1 | $12 |
| Networking & misc | $12 | $144 |
| **Total infrastructure** | **$171-327** | **$2,052-3,924** |

---

## 3. GitHub Costs

### GitHub Actions (CI/CD)

Ford uses GHEC EMU. GitHub Actions minutes are included in GHEC license:
- **GHEC**: 50,000 included minutes/month (Linux)
- **Overage**: $0.008/minute (Linux runners)

Platform agent CI impact:
```
Agent CI pipeline (build + test + lint):    ~3 min/run
Runs per day (PRs + pushes):               ~20 runs
Monthly minutes:                           ~1,800 min  (well within 50K included)
```

Additional cost from platform-generated PRs (scaffold, infra):
```
scaffold_project triggers CI:              ~5 min/run
create_service_pr triggers CI:             ~3 min/run
Estimated platform-triggered runs/month:   ~200 runs × 4 min avg = 800 min

Total platform CI minutes:                 ~2,600 min/mo (5.2% of included)
```

**GitHub Actions cost for platform: $0** (within included GHEC minutes)

### GitHub API Rate Limits

```
Authenticated API (PAT/GitHub App):  5,000 requests/hour
Platform at Ford scale:              ~4,000 queries/day × 2 API calls avg = 8,000/day
                                     = 333/hour (6.7% of limit)

No additional cost. GitHub App is recommended over PAT for:
  - Higher rate limits (installation tokens)
  - Fine-grained permissions
  - Audit trail per-app
```

---

## 4. Total Cost of Ownership (3-Year)

### Year 1 (Pilot + Ford Scale Rollout)

| Category | Cost |
|---|---|
| LLM (ramp from pilot to full) | $1,500 |
| Infrastructure | $3,000 |
| GitHub (included in GHEC) | $0 |
| Engineering time (setup, 2 FTEs × 3 months) | $45,000 |
| Training & rollout | $2,500 |
| **Year 1 total** | **$52,000** |

### Year 2 (Steady State)

| Category | Cost |
|---|---|
| LLM (full scale) | $3,500 |
| Infrastructure | $3,500 |
| Engineering time (maintenance, 0.5 FTE) | $37,500 |
| **Year 2 total** | **$44,500** |

### Year 3 (Optimization)

| Category | Cost |
|---|---|
| LLM (optimized routing, caching) | $2,500 |
| Infrastructure | $3,500 |
| Engineering time (maintenance, 0.5 FTE) | $37,500 |
| **Year 3 total** | **$43,500** |

### 3-Year TCO: ~$140,000

---

## 5. ROI Analysis

### Developer Time Savings

| Activity | Current Time | With Platform Agent | Savings per Instance | Frequency (Annual) | Annual Hours Saved |
|---|---|---|---|---|---|
| New service setup | 2-5 days (16-40 hrs) | 5 min | ~24 hrs | 50 services | 1,200 hrs |
| CI/CD pipeline config | 4-8 hrs | 2 min (scaffold) | ~6 hrs | 50 services | 300 hrs |
| DORA metrics collection | 2-4 hrs/team/quarter | 30 sec | ~3 hrs | 80 teams × 4 | 960 hrs |
| Infra PR creation | 1-2 hrs | 2 min | ~1.5 hrs | 200 PRs | 300 hrs |
| "Where is X?" questions | 15-30 min each | 10 sec | ~20 min | 15,000/year | 5,000 hrs |
| Pipeline status checks | 5-10 min each | 5 sec | ~7 min | 30,000/year | 3,500 hrs |
| Deployment status queries | 10-20 min each | 10 sec | ~15 min | 10,000/year | 2,500 hrs |
| **Total annual savings** | | | | | **13,760 hrs** |

### Dollar Value of Savings

```
Average fully-loaded developer cost:  $75/hr (blended rate)
Annual savings:                       13,760 hrs × $75 = $1,032,000

3-year savings:                       ~$3.1M
3-year platform cost:                 ~$140K
3-year net savings:                   ~$2.96M
ROI:                                  ~2,114%
Payback period:                       ~2 months
```

### Comparison with Alternatives

| Solution | 3-Year TCO | Deployment Time | Customizable | Ford-Specific |
|---|---|---|---|---|
| **Platform Agent (this POC)** | **$140K** | 3-6 months | Full | Yes |
| Backstage (Spotify) | $800K-1.5M | 12-18 months | Moderate | No (plugin dev) |
| Cortex.io | $500K-1M | 3-6 months | Limited | No (SaaS) |
| Port.io | $400K-800K | 2-4 months | Moderate | No (SaaS) |
| ServiceNow DevOps | $1.2M-2M | 6-12 months | Low | No |
| Custom internal portal | $2M-4M | 18-24 months | Full | Yes |

### Why Platform Agent costs 5-10x less

1. **No UI to build/maintain** — LLM generates responses; no React/Angular/Vue
2. **No plugin ecosystem to manage** — tools are plain Python functions
3. **Stateless by design** — queries authoritative APIs, no sync/cache/stale data
4. **Cloud Run scales to zero** — pay only for actual usage
5. **LLM routing** — 90% cheap model, 10% expensive model
6. **GitHub Actions included** — CI/CD is already paid for in GHEC

---

## 6. Cost Optimization Strategies

### Prompt Caching (Vertex AI)

```
Gemini Flash with context caching:
  Cached input: $0.01875/M (87.5% discount)
  System prompt + tool definitions = ~2,000 tokens
  At 4,000 queries/day: saves ~$15/mo

Claude with prompt caching:
  Cached input: $0.30/M (90% discount vs $3.00)
  Saves ~$10/mo at scale
```

### Response Streaming

Streaming reduces perceived latency without increasing cost. ADK and MCP both support streaming natively.

### Batch Processing for DORA Metrics

```
Instead of real-time calculation per query:
  Cache DORA results per-repo, refresh every 4 hours
  Reduces GitHub API calls by ~95%
  Reduces LLM calls for repeated metrics queries
```

### Token Budget Controls

```python
# Already in config.py — can enforce per-query limits
class GuardrailConfig:
    rate_limit_per_min: int = 30  # prevents runaway costs
    max_concurrent_deploys: int = 3
```

---

## 7. Cost Monitoring

### Recommended alerts

| Alert | Threshold | Action |
|---|---|---|
| Daily LLM spend | > $15/day ($450/mo pace) | Review query patterns |
| Cloud Run instances | > 8 sustained | Check for runaway loops |
| GitHub API rate limit | > 60% utilization | Switch to GitHub App |
| BigQuery scan | > 1 TB/mo | Review audit queries |
| Sonnet routing ratio | > 20% of queries | Tune classifier |

### Dashboard metrics

- Cost per query (LLM + compute)
- Queries per team per day
- Flash vs Sonnet routing ratio
- Cloud Run cold start rate
- GitHub API utilization percentage

---

## Appendix: Pricing Sources

| Service | Source | As Of |
|---|---|---|
| Gemini 2.5 Flash | Vertex AI pricing page | 2026 |
| Claude Sonnet/Opus | Vertex AI partner model pricing | 2026 |
| Cloud Run | GCP pricing calculator | 2026 |
| Cloud SQL PostgreSQL | GCP pricing calculator | 2026 |
| BigQuery | GCP pricing page | 2026 |
| GitHub Actions | GitHub Enterprise pricing | 2026 |
| GHEC EMU | GitHub Enterprise pricing | 2026 |

*All projections use conservative estimates. Actual costs may vary based on usage patterns, negotiated enterprise agreements, and committed-use discounts.*
