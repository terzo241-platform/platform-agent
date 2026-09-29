# Ford Platform Agent — Enterprise Scale Guide

> Adaptation guide: POC on personal GitHub to Ford GHEC EMU at enterprise scale.

## 1. Architecture: POC vs Ford Production

```
POC (Today)                          Ford Production
─────────────                        ────────────────
GitHub.com (personal org)     →      GHEC EMU (ford-motor-company)
5 repos, 1 org                →      80+ teams, 1000+ repos
Claude Code (local MCP)       →      Cloud Run (central agent API)
In-memory sessions            →      Cloud SQL PostgreSQL
stdout audit logs             →      BigQuery + Cloud Trace
Manual env vars               →      Secret Manager + Workload Identity
No network controls           →      VPC-SC + Private Google Access
Single developer              →      400+ developers, SSO/SAML
```

### What Changes, What Stays

| Layer | POC | Ford | Change Required |
|---|---|---|---|
| Tool functions (24 tools) | Same | Same | None — tools are API-agnostic |
| Provider registry | GitHub only | GitHub + Tekton + ArgoCD | Config change only |
| MCP server | stdio transport | HTTP + API Gateway | Transport config |
| ADK agent | Gemini Flash | Gemini Flash + Claude Sonnet (router) | Add classifier |
| Config | .env file | Secret Manager + env-specific configs | Infra setup |
| Auth | GitHub PAT | GitHub App + Workload Identity | New auth flow |
| Networking | Public internet | VPC-SC perimeter | GCP infra |
| Observability | None | BigQuery + Cloud Trace + dashboards | New components |

---

## 2. GitHub Enterprise Cloud (GHEC EMU)

### EMU-Specific Considerations

GHEC EMU (Enterprise Managed Users) means:
- User identities provisioned via SAML/SCIM from Azure AD
- No personal accounts — all users are `_ford` suffix
- Repository visibility limited to enterprise members
- GitHub Apps scoped to enterprise, not individual orgs

### GitHub App Setup (Recommended over PAT)

```yaml
GitHub App Configuration:
  name: ford-platform-agent
  owner: ford-motor-company (enterprise)
  permissions:
    repository:
      contents: read/write        # scaffold, create PRs
      pull_requests: read/write   # create/merge PRs
      actions: read               # pipeline status
      checks: read                # CI results
      metadata: read              # repo info
    organization:
      members: read               # team membership for DORA
      administration: read        # branch protection audit
  webhook_events:
    - pull_request
    - workflow_run
    - push
  installation: all repositories  # or selected repos
```

### Why GitHub App over PAT

| Factor | PAT | GitHub App |
|---|---|---|
| Rate limit | 5,000 req/hr | 15,000 req/hr per installation |
| Identity | Tied to human user | Service identity |
| Audit trail | Shows as user action | Shows as app action |
| Rotation | Manual, breaks on expiry | Auto-renewed installation tokens |
| Scope | Too broad or too narrow | Fine-grained, per-permission |
| EMU compatible | Yes, but tied to managed user | Yes, enterprise-scoped |

### Multi-Org Support

Ford may have multiple GitHub organizations under one enterprise:

```python
# Provider registry already handles this
class GitHubConfig:
    api_url: str = "https://api.github.com"  # or GHES URL
    org: str = ""  # primary org
    
    # Extension for Ford:
    # enterprise: str = "ford-motor-company"
    # orgs: list[str] = ["ford-platform", "ford-marketing", "ford-sales"]
    # ProviderRegistry routes by repo prefix → org
```

### Org-Level Policies

Ford GHEC EMU likely has:
- IP allow list — agent must run from allowed CIDR
- SAML enforcement — all API access must go through SSO
- Repository creation restrictions — `scaffold_project` may need admin App permissions

---

## 3. Identity & Access Management

### Authentication Flow

```
Developer → Slack/Chat/IDE
       │
       ▼
  Identity Layer (Ford SSO)
       │
       ├─ Slack: Slack Enterprise Grid SSO → Azure AD SAML
       ├─ Chat API: GCP IAP (Identity-Aware Proxy) → Azure AD OIDC
       ├─ CLI: gcloud auth → Workforce Identity Federation
       └─ MCP (Claude Code): Claude license → no platform auth needed
       │
       ▼
  Platform Agent
       │
       ├─ Extract user identity from token/header
       ├─ Map to GitHub identity (EMU username)
       ├─ Apply guardrails (env protection, rate limits)
       └─ Audit: log (who, what, when, outcome)
```

### Workload Identity (No Service Account Keys)

```bash
# Create GCP service account
gcloud iam service-accounts create platform-agent-sa \
  --project=PROJECT_ID

# Grant Vertex AI access
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:platform-agent-sa@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/aiplatform.user"

# Grant BigQuery access (audit)
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:platform-agent-sa@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/bigquery.dataEditor"

# Grant Secret Manager access
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:platform-agent-sa@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/secretmanager.secretAccessor"

# Grant Cloud Trace access (OTel)
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="serviceAccount:platform-agent-sa@PROJECT_ID.iam.gserviceaccount.com" \
  --role="roles/cloudtrace.agent"

# Bind K8s SA to GCP SA (if using GKE)
gcloud iam service-accounts add-iam-policy-binding \
  platform-agent-sa@PROJECT_ID.iam.gserviceaccount.com \
  --role="roles/iam.workloadIdentityUser" \
  --member="serviceAccount:PROJECT_ID.svc.id.goog[platform-agent/platform-agent-sa]"
```

### Role-Based Access Control

```python
# Extend GuardrailConfig for Ford
FORD_RBAC = {
    "platform-admin": {
        "can_approve_prod": True,
        "can_scaffold": True,
        "can_view_all_metrics": True,
        "rate_limit_per_min": 60,
    },
    "team-lead": {
        "can_approve_prod": True,        # own team's repos only
        "can_scaffold": True,
        "can_view_all_metrics": False,   # own team only
        "rate_limit_per_min": 30,
    },
    "developer": {
        "can_approve_prod": False,
        "can_scaffold": True,
        "can_view_all_metrics": False,
        "rate_limit_per_min": 20,
    },
    "viewer": {
        "can_approve_prod": False,
        "can_scaffold": False,
        "can_view_all_metrics": False,
        "rate_limit_per_min": 10,
    },
}
```

---

## 4. Network & Security

### VPC Service Controls

```
                    VPC-SC Perimeter
┌──────────────────────────────────────────────────┐
│                                                  │
│  Cloud Run / GKE Cluster                         │
│    └── Platform Agent                            │
│          ├── → Vertex AI (Gemini/Claude) ✓       │
│          ├── → BigQuery (audit) ✓                │
│          ├── → Secret Manager ✓                  │
│          ├── → Cloud SQL (sessions) ✓            │
│          └── → Cloud Trace (OTel) ✓              │
│                                                  │
└──────────────────────────────────────────────────┘
           │
           │ Egress rule (specific)
           ▼
    ┌──────────────────┐
    │ api.github.com   │  ← Only GitHub API
    │ argocd.internal  │  ← Internal ArgoCD
    │ tekton.internal  │  ← Internal Tekton
    └──────────────────┘
```

Required VPC-SC egress rules:
```yaml
egress_policies:
  - egress_from:
      identity_type: ANY_SERVICE_ACCOUNT
      sources:
        - resource: projects/PRJ_NUMBER
    egress_to:
      resources: ["*"]
      operations:
        - service_name: "aiplatform.googleapis.com"
        - service_name: "bigquery.googleapis.com"
        - service_name: "secretmanager.googleapis.com"
        - service_name: "sqladmin.googleapis.com"
        - service_name: "cloudtrace.googleapis.com"
        - service_name: "run.googleapis.com"
```

### Private Google Access

```
Cloud Run → Vertex AI (Gemini):     via Private Google Access (no public internet)
Cloud Run → Cloud SQL:              via Private IP (VPC connector)
Cloud Run → BigQuery:               via Private Google Access
Cloud Run → Secret Manager:         via Private Google Access
Cloud Run → GitHub API:             via Cloud NAT (requires egress to public GitHub)
```

### Secret Management

```yaml
# Secret Manager secrets (not env vars)
secrets:
  - name: github-app-private-key
    version: latest
    mount: /secrets/github/private-key.pem

  - name: github-app-id
    version: latest
    env: GITHUB_APP_ID

  - name: slack-bot-token
    version: latest
    env: SLACK_BOT_TOKEN

  - name: slack-signing-secret
    version: latest
    env: SLACK_SIGNING_SECRET

  - name: argocd-token
    version: latest
    env: ARGOCD_TOKEN
```

### Data Classification

| Data Type | Classification | Storage | Retention |
|---|---|---|---|
| User prompts | Internal | BigQuery (audit) | 90 days |
| Tool responses | Internal | Not stored (stateless) | N/A |
| LLM responses | Internal | BigQuery (audit) | 90 days |
| Session context | Internal | Cloud SQL | 24 hrs TTL |
| GitHub tokens | Confidential | Secret Manager | Rotated quarterly |
| Audit logs | Compliance | BigQuery → Cloud Storage | 7 years |
| DORA metrics | Internal | Not stored (calculated on demand) | N/A |

### Air-Gapped Provider Mirror

Ford's clusters are typically egress-restricted. All dependencies baked into container:

```dockerfile
FROM python:3.11-slim
COPY pyproject.toml .
RUN pip install --no-cache-dir .
# All dependencies installed at build time, no runtime downloads
```

For Terraform providers (used by `create_service_pr`):
```hcl
# .terraformrc on the pod
provider_installation {
  filesystem_mirror {
    path    = "/usr/share/terraform/plugins"
    include = ["registry.terraform.io/hashicorp/*"]
  }
}
```

---

## 5. Compliance & Governance

### OWASP LLM Top 10 (2025) Mapping

| Risk | Mitigation in Platform Agent |
|---|---|
| LLM01: Prompt Injection | Tool-only architecture — LLM can only call registered tools, not arbitrary code |
| LLM02: Insecure Output | Tools return structured data; LLM formats but can't execute |
| LLM03: Training Data Poisoning | Using commercial models (Gemini, Claude) — no custom training |
| LLM04: Model DoS | Rate limiting (30 req/min default), token budget per query |
| LLM05: Supply Chain | Pinned dependencies, air-gapped provider mirrors, SBOM |
| LLM06: Sensitive Info Disclosure | Tools enforce access control; LLM sees only API responses |
| LLM07: Insecure Plugin Design | ToolAnnotations (READ_ONLY, WRITE_SAFE, DESTRUCTIVE), require_confirmation |
| LLM08: Excessive Agency | Guardrail callbacks, human-in-the-loop for prod actions |
| LLM09: Overreliance | DORA metrics from authoritative APIs, not LLM-generated data |
| LLM10: Model Theft | Vertex AI (managed), no model weights stored locally |

### Ford Compliance Requirements

| Requirement | Implementation |
|---|---|
| SOX (financial controls) | Separation of duties in approve_and_merge; create != approve |
| Data residency | Vertex AI region: us-central1 (or Ford-approved region) |
| PII protection | No PII in prompts by design; tools query code/CI, not customer data |
| Change management | All changes via PRs with branch protection; agent creates PR, human approves |
| Audit trail | Every tool invocation logged: identity, timestamp, tool, args, outcome |
| Incident response | Rate limiting prevents runaway; kill switch via Cloud Run revision |

### AI Governance Checklist

- [ ] AI use case registered with Ford AI governance board
- [ ] Data flow diagram approved by InfoSec
- [ ] Vertex AI project within Ford's approved GCP organization
- [ ] Model selection approved (Gemini Flash — Google first-party)
- [ ] Fallback model approved (Claude Sonnet via Vertex AI — not direct API)
- [ ] Prompt templates reviewed for bias and safety
- [ ] Rate limits configured per Ford policy
- [ ] Audit retention meets legal hold requirements
- [ ] Disaster recovery plan documented
- [ ] Incident response runbook for AI-specific failures

---

## 6. Deployment Architecture (Ford Scale)

### Production Topology

```
                           ┌──────────────────────────────────┐
                           │  GCP Project: ford-platform-prd  │
                           │                                  │
  Slack Enterprise Grid ──►│  Cloud Run: platform-agent-api   │
  (webhook)                │    ├─ min 2, max 20 instances    │
                           │    ├─ 1 vCPU, 1 GiB each        │
  Chat UI (internal) ─────►│    ├─ Workload Identity          │
  (IAP-protected)          │    └─ VPC connector              │
                           │         │                        │
  Claude Code (MCP) ──────►│  Cloud Run: mcp-server-http      │
  (API Gateway)            │    ├─ min 1, max 10 instances    │
                           │    └─ HTTP transport             │
                           │         │                        │
                           │    ┌────┴────┐                   │
                           │    ▼         ▼                   │
                           │  Cloud SQL  BigQuery             │
                           │  (sessions) (audit)              │
                           │         │                        │
                           │    Secret Manager                │
                           │    (tokens, keys)                │
                           └──────────────────────────────────┘
                                     │
                    ┌────────────────┼────────────────┐
                    ▼                ▼                ▼
              GitHub API       ArgoCD API       Tekton API
              (GHEC EMU)       (internal)       (internal)
```

### Multi-Environment

```yaml
Environments:
  dev:
    project: ford-platform-dev
    gemini_model: gemini-2.5-flash
    rate_limit: 60/min
    cloud_run_min: 0        # scale to zero OK
    cloud_sql: none         # in-memory sessions
    bigquery: dev dataset
    github_org: ford-platform-dev

  staging:
    project: ford-platform-stg
    gemini_model: gemini-2.5-flash
    rate_limit: 30/min
    cloud_run_min: 1
    cloud_sql: shared small
    bigquery: stg dataset
    github_org: ford-platform (staging repos)

  production:
    project: ford-platform-prd
    gemini_model: gemini-2.5-flash + claude-sonnet-5 (router)
    rate_limit: 30/min
    cloud_run_min: 2        # always warm
    cloud_sql: HA (2 zones)
    bigquery: prd dataset + 7yr retention
    github_org: ford-motor-company
```

### GKE Deployment (Recommended for Ford)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: platform-agent
  namespace: platform-agent
spec:
  replicas: 2
  template:
    spec:
      serviceAccountName: platform-agent-sa  # Workload Identity
      containers:
      - name: agent
        image: REGION-docker.pkg.dev/PROJECT/platform-agent/agent:latest
        ports:
        - containerPort: 8080   # Chat API
        - containerPort: 8081   # MCP HTTP (optional)
        env:
        - name: GITHUB_APP_ID
          valueFrom:
            secretKeyRef:
              name: platform-agent-secrets
              key: github-app-id
        - name: GOOGLE_CLOUD_PROJECT
          value: PROJECT_ID
        resources:
          requests:
            cpu: 500m
            memory: 512Mi
          limits:
            cpu: 2000m
            memory: 1Gi
```

### Cloud Run Alternative (Simpler)

```bash
gcloud run deploy platform-agent \
  --image=REGION-docker.pkg.dev/PROJECT/platform-agent/agent:latest \
  --service-account=platform-agent-sa@PROJECT_ID.iam.gserviceaccount.com \
  --vpc-connector=platform-agent-connector \
  --vpc-egress=private-ranges-only \
  --min-instances=2 \
  --max-instances=20 \
  --memory=1Gi \
  --cpu=1 \
  --port=8080 \
  --no-allow-unauthenticated
```

---

## 7. Scaling Considerations

### GitHub API Rate Limits at Ford Scale

```
80 teams × 20 queries/day × 2 API calls = 3,200 API calls/day
= 133 calls/hour (well under 5,000/hr PAT or 15,000/hr App)

Peak (all teams active, 9-10 AM): 
  ~800 calls/hour (still under limits)

DORA metrics batch (nightly):
  80 repos × 5 metrics × ~10 API calls = 4,000 calls
  Run over 1 hour = 4,000/hr (fits within limits)
```

### Memory and CPU

```
Single agent pod:
  Idle: ~150 MB, 0.1 vCPU
  Active query: ~300 MB, 0.5 vCPU
  Peak (concurrent DORA calc): ~500 MB, 1.0 vCPU

Recommended: 2 replicas, 1 GiB / 1 vCPU each
  Handles: ~50 concurrent queries
  At Ford scale: ~4 concurrent during peak = comfortable headroom
```

---

## 8. Migration Path: POC → Pilot → Production

### Phase 1: POC Validation (Current — Weeks 1-4)

- [x] Build all 24 tools (274 tests passing)
- [x] MCP + ADK dual surface working
- [x] Slack bot adapter ready
- [x] DORA metrics engine with benchmarks
- [x] Scaffold with 3 templates (Python/Node/Java)
- [x] Architecture + cost + governance documentation
- [ ] Demo to leadership
- [ ] Collect feedback

### Phase 2: Pilot (Weeks 5-12)

```
Week 5-6: Infrastructure
  - [ ] Create GCP project (ford-platform-dev)
  - [ ] Deploy Cloud Run service
  - [ ] Set up GitHub App on GHEC EMU
  - [ ] Configure Workload Identity
  - [ ] Set up Secret Manager
  - [ ] Configure VPC connector + Cloud NAT
  - [ ] Deploy Cloud SQL (dev, non-HA)

Week 7-8: Integration
  - [ ] Connect to GHEC EMU (3 pilot team repos)
  - [ ] Set up Slack channel + bot
  - [ ] Configure ArgoCD provider (if applicable)
  - [ ] Set up BigQuery audit dataset
  - [ ] Implement LLM router (Flash + Sonnet)
  - [ ] Load test: 500 queries/day

Week 9-10: Pilot Teams
  - [ ] Onboard 3 pilot teams (10-15 developers)
  - [ ] Run DORA baseline measurement
  - [ ] Collect feedback
  - [ ] Measure: adoption rate, query patterns, error rate

Week 11-12: Hardening
  - [ ] VPC-SC perimeter setup
  - [ ] IAP for Chat API
  - [ ] Cloud SQL HA
  - [ ] Monitoring dashboards
  - [ ] Incident response runbook
  - [ ] Security review with InfoSec
```

### Phase 3: Production Rollout (Weeks 13-24)

```
Week 13-16: Scale
  - [ ] Onboard 20 teams
  - [ ] Performance tuning (caching, routing)
  - [ ] Add Tekton provider (for Tekton teams)
  - [ ] Add ArgoCD provider (for GitOps teams)

Week 17-24: Full Rollout
  - [ ] Onboard remaining 60 teams
  - [ ] DORA metrics comparison (before/after)
  - [ ] Executive dashboard
  - [ ] Knowledge base updates
  - [ ] Platform team training
```

### Phase 4: Optimization (Months 7-12)

- [ ] Prompt caching optimization
- [ ] Custom templates per business unit
- [ ] Self-service template contribution
- [ ] Cross-org DORA benchmarking
- [ ] Multi-agent orchestration (complex queries)
- [ ] OTel dashboards in Cloud Monitoring

---

## 9. Operational Runbook

### Health Checks

```bash
# Cloud Run health
curl -s https://platform-agent.ford.internal/health | jq .

# GitHub API connectivity
curl -s -H "Authorization: Bearer $TOKEN" \
  https://api.github.com/rate_limit | jq .rate

# Vertex AI connectivity
curl -s -H "Authorization: Bearer $(gcloud auth print-access-token)" \
  "https://us-central1-aiplatform.googleapis.com/v1/projects/PROJECT/locations/us-central1/publishers/google/models/gemini-2.5-flash:generateContent" \
  -d '{"contents":[{"parts":[{"text":"ping"}]}]}' | jq .candidates[0].content
```

### Kill Switch

```bash
# Immediate: route all traffic to maintenance revision
gcloud run services update-traffic platform-agent-api \
  --to-revisions=maintenance-rev=100 \
  --region=us-central1

# Gradual: set max instances to 0
gcloud run services update platform-agent-api \
  --max-instances=0 \
  --region=us-central1
```

### Incident Response

| Severity | Symptoms | Action |
|---|---|---|
| P1: Wrong changes | Incorrect PRs, wrong repos | Kill switch → investigate audit logs |
| P2: LLM cost spike | Daily spend > $15 | Reduce rate limit → check routing ratio |
| P3: API rate limited | 429 responses | Check for loops → reduce polling |
| P4: Slow responses | > 5s p95 latency | Check cold starts → increase min instances |

---

## 10. Team Structure

### Recommended Platform Team

| Role | FTE | Responsibility |
|---|---|---|
| Platform Engineer (Lead) | 1.0 | Architecture, roadmap, LLM integration |
| Platform Engineer | 1.0 | Tool development, provider maintenance |
| SRE | 0.5 | Infrastructure, monitoring, incidents |
| Security | 0.25 | AI governance, compliance reviews |
| **Total** | **2.75** | |

### Compared to Alternatives

| Approach | FTEs | Why |
|---|---|---|
| Internal portal (Backstage) | 6-10 | UI development, plugin ecosystem, data sync |
| Platform Agent (this POC) | 2-3 | No UI, stateless tools, managed infra |
| Vendor portal (Cortex/Port) | 2-3 + $500K/yr license | Vendor relationship, customization |

---

## 11. Risks & Mitigations

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| LLM hallucination on infra | Medium | High | All writes go through PR flow (human review) |
| API key exposure | Low | Critical | GitHub App + Workload Identity (no static keys) |
| Cost overrun | Low | Medium | Rate limiter + budget alerts + LLM router |
| VPC-SC blocks Vertex AI | Medium | High | Test egress rules in nonprod first |
| Low developer adoption | Medium | Medium | Start with DORA metrics (read-only, high value) |
| Tekton migration complexity | High | Medium | Provider abstraction isolates impact |
| GHEC EMU token expiry | Medium | Medium | GitHub App with auto-renewal; monitoring on auth failures |
| Prompt injection via repo names | Low | Medium | Tool-only architecture; LLM can't execute arbitrary code |

---

## 12. Decision Log

Decisions for Ford adaptation — to be made with stakeholders:

1. **GitHub App vs PAT** — App recommended, needs org admin approval
2. **GKE vs Cloud Run** — GKE if existing cluster, Cloud Run if greenfield
3. **Shared vs dedicated VPC** — depends on network team policy
4. **BigQuery dataset location** — US multi-region vs specific region (compliance)
5. **Slack workspace** — which workspace, who gets access
6. **First pilot team** — ideally one with both GHA and Tekton for full coverage
7. **DORA metrics visibility** — team-only or leadership dashboard
8. **LLM model approvals** — Gemini Flash (Google 1P) + Claude Sonnet (via Vertex)
9. **Data retention policy** — 90 days (audit) vs 7 years (compliance)
10. **Kill switch authority** — who can invoke, under what conditions

---

## Appendix: Configuration Template

```env
# ── Ford Production .env template ──

# Agent
FORD_AGENT_MODEL=gemini-2.5-flash
FORD_AGENT_REASONING_MODEL=claude-sonnet-5
FORD_AGENT_APP_NAME=ford-platform-agent

# GitHub (use GitHub App, not PAT)
GITHUB_APP_ID=123456
GITHUB_APP_INSTALLATION_ID=789012
GITHUB_APP_PRIVATE_KEY_PATH=/secrets/github/private-key.pem
GITHUB_ORG=ford-motor-company
GITHUB_API_URL=https://api.github.com

# ArgoCD
ARGOCD_SERVER=argocd.ford.internal
ARGOCD_USE_KUBE_AUTH=true
ARGOCD_INSECURE=false

# Tekton
TEKTON_API_URL=https://tekton.ford.internal
TEKTON_NAMESPACE=tekton-pipelines

# Guardrails
FORD_APPROVAL_REQUIRED_ENVS=prod,production,prd
FORD_MAX_CONCURRENT_DEPLOYS=3
FORD_RATE_LIMIT_PER_MIN=30

# Audit
FORD_AUDIT_DATASET=platform_agent_audit
FORD_AUDIT_TABLE=actions
GOOGLE_CLOUD_PROJECT=ford-platform-prd

# Slack
SLACK_BOT_TOKEN=xoxb-...
SLACK_SIGNING_SECRET=...
SLACK_APP_TOKEN=xapp-...
SLACK_CHAT_API_URL=https://platform-agent.ford.internal

# Chat API
FORD_CHAT_HOST=0.0.0.0
FORD_CHAT_PORT=8080
FORD_CHAT_CORS_ORIGINS=https://chat.ford.internal
FORD_CHAT_API_KEYS=key1,key2
FORD_CHAT_SESSION_TTL_HOURS=8
FORD_CHAT_MAX_SESSIONS_PER_USER=5
FORD_CHAT_RATE_LIMIT_PER_MINUTE=30
```
