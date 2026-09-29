# Ford-Scale Adaptation Guide

How to take this POC from personal GitHub to Ford's enterprise environment.

## Environment Differences

| Dimension | POC (terzo241-platform) | Ford Production |
|---|---|---|
| GitHub | Personal org, 5 repos | GHEC EMU, 80+ teams, SAML SSO |
| Network | Public internet | VPC-SC perimeter, egress controls |
| Compute | Local/Cloud Run (public) | GKE private cluster, air-gapped |
| LLM access | Direct Vertex AI API | Vertex AI inside VPC-SC, private endpoints |
| Identity | GitHub PAT | Workload Identity Federation + SAML |
| Providers | GitHub Actions only | GitHub Actions + Tekton + ArgoCD |
| Secrets | .env file | Secret Manager + Workload Identity |
| Audit | structlog to stdout | BigQuery + Cloud Logging, SOX retention |

## GHEC EMU Adaptations

### Authentication

POC uses a GitHub PAT. Ford GHEC EMU requires:

```python
# config.py adaptation
class GitHubConfig:
    # POC: personal access token
    # token: str  

    # Ford: GitHub App installation token (auto-rotated)
    app_id: int
    private_key_path: str
    installation_id: int
```

GitHub App provides:
- Fine-grained permissions per-repo
- Higher rate limits (15,000 requests/hour per installation vs 5,000 for PAT)
- Audit trail per-app in GHEC
- No personal token leakage risk

### Identity Propagation

```
Developer (SAML SSO)
  → Slack/Chat/IDE (identity from SSO)
    → Platform Agent (extract identity from auth context)
      → Audit log (who did what, tied to corporate identity)
        → BigQuery (compliance, SOX)
```

The `after_agent_audit()` callback needs to extract corporate identity:

```python
# POC: user = github_username
# Ford: user = SAML assertion subject (email)
def extract_identity(context):
    if slack_context:
        return slack_user_to_corporate_email(context.user_id)
    if chat_api_context:
        return context.auth_header.subject  # from JWT
    if mcp_context:
        return os.environ.get("USER")  # local user
```

### Org-Level Policies

Ford GHEC EMU likely has:
- IP allow list — agent must run from allowed CIDR
- SAML enforcement — all API access must go through SSO
- Repository creation restrictions — `scaffold_project` may need admin App permissions

## VPC Service Controls

### Perimeter Configuration

```
                    VPC-SC Perimeter
┌──────────────────────────────────────────────────┐
│                                                  │
│  GKE Cluster                                     │
│    └── Platform Agent pod                        │
│          ├── → Vertex AI (Gemini/Claude) ✓       │
│          ├── → BigQuery (audit) ✓                │
│          ├── → Secret Manager ✓                  │
│          └── → Cloud SQL (sessions) ✓            │
│                                                  │
│  Cloud Run (alternative)                         │
│    └── same connectivity                         │
│                                                  │
└──────────────────────────────────────────────────┘
           │
           │ Egress rule (specific)
           ▼
    ┌──────────────────┐
    │ api.github.com   │  ← Only GitHub API
    │ *.actions.        │
    └──────────────────┘
```

Required VPC-SC egress rules:
```
egress_policies:
  - egress_from:
      identity_type: ANY_SERVICE_ACCOUNT
      sources:
        - resource: projects/PRJ_NUMBER
    egress_to:
      resources: ["*"]
      operations:
        - service_name: "vertex-ai.googleapis.com"
          method_selectors: ["*"]
        - service_name: "bigquery.googleapis.com"
          method_selectors: ["*"]

  # GitHub API (external)
  - egress_from:
      identity_type: ANY_SERVICE_ACCOUNT
    egress_to:
      external_resources: ["api.github.com"]
```

### Private Google Access

The agent pod needs Private Google Access for Vertex AI:

```yaml
# GKE subnet configuration
subnetwork:
  privateIpGoogleAccess: true
  # OR
  privateIpv6GoogleAccess: true
```

## Air-Gapped Provider Mirror

Ford's GKE clusters are typically egress-restricted. Python packages need to be mirrored:

```bash
# Build container with all dependencies baked in
FROM python:3.11-slim
COPY pyproject.toml .
RUN pip install --no-cache-dir . 
# All dependencies installed at build time, no runtime downloads
```

For Terraform providers (used by `create_service_pr`):
```
# .terraformrc on the pod
provider_installation {
  filesystem_mirror {
    path    = "/usr/share/terraform/plugins"
    include = ["registry.terraform.io/hashicorp/*"]
  }
}
```

## Deployment Architecture

### Option A: GKE (Recommended for Ford)

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

### Workload Identity Setup

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

# Bind K8s SA to GCP SA
gcloud iam service-accounts add-iam-policy-binding \
  platform-agent-sa@PROJECT_ID.iam.gserviceaccount.com \
  --role="roles/iam.workloadIdentityUser" \
  --member="serviceAccount:PROJECT_ID.svc.id.goog[platform-agent/platform-agent-sa]"
```

### Option B: Cloud Run (Simpler, if allowed)

```bash
gcloud run deploy platform-agent \
  --image=REGION-docker.pkg.dev/PROJECT/platform-agent/agent:latest \
  --service-account=platform-agent-sa@PROJECT_ID.iam.gserviceaccount.com \
  --vpc-connector=platform-agent-connector \
  --vpc-egress=private-ranges-only \
  --min-instances=1 \
  --max-instances=10 \
  --memory=512Mi \
  --cpu=1 \
  --port=8080 \
  --no-allow-unauthenticated
```

## Scaling Considerations

### GitHub API Rate Limits at Ford Scale

```
80 teams × 20 queries/day × 2 API calls = 3,200 API calls/day
= 133 calls/hour (well under 5,000/hr PAT or 15,000/hr App)

Peak (all teams active, 9-10 AM): 
  ~800 calls/hour (still under limits)

DORA metrics batch (nightly):
  80 repos × 5 metrics × ~10 API calls = 4,000 calls
  Run over 1 hour = 4,000/hr (fits within PAT limit, comfortable with App)
```

### Memory and CPU

```
Single agent pod:
  Idle: ~150 MB, 0.1 vCPU
  Active query: ~300 MB, 0.5 vCPU
  Peak (concurrent DORA calc): ~500 MB, 1.0 vCPU

Recommended: 2 replicas, 512 MiB / 1 vCPU each
  Handles: ~50 concurrent queries
  At Ford scale: ~4 concurrent during peak = comfortable headroom
```

## Migration Path

### Phase 1: POC Validation (Current — Weeks 1-4)

- [x] Build all 24 tools
- [x] MCP + ADK dual surface
- [x] DORA metrics engine
- [x] Governance guardrails
- [ ] Demo to leadership
- [ ] Collect feedback

### Phase 2: Pilot (Weeks 5-12)

- [ ] Deploy to Ford GKE (single team)
- [ ] GitHub App setup (replace PAT)
- [ ] Workload Identity configuration
- [ ] VPC-SC egress rules
- [ ] BigQuery audit table
- [ ] Connect to 1-2 real repos
- [ ] Measure: time saved, accuracy, adoption

### Phase 3: Rollout (Weeks 13-24)

- [ ] Provider integrations (Tekton, ArgoCD)
- [ ] LLM router implementation (Flash/Sonnet)
- [ ] DORA metrics batch pipeline
- [ ] Slack bot deployment
- [ ] Team-by-team onboarding (10 teams/sprint)
- [ ] Backstage comparison metrics

### Phase 4: Optimization (Months 7-12)

- [ ] Prompt caching
- [ ] Response streaming
- [ ] Multi-agent orchestration (complex queries)
- [ ] Custom Ford knowledge packs (per-team golden paths)
- [ ] OTel dashboards in Cloud Monitoring

## Risk Mitigation

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| LLM hallucination on infra | Medium | High | All writes go through PR flow (human review) |
| API key exposure | Low | Critical | GitHub App + Workload Identity (no static keys) |
| Cost overrun | Low | Medium | Rate limiter + budget alerts + LLM router |
| VPC-SC blocks Vertex AI | Medium | High | Test egress rules in nonprod first |
| Low developer adoption | Medium | Medium | Start with DORA metrics (read-only, high value) |
| Tekton migration complexity | High | Medium | Provider abstraction isolates impact |

## Decision Log

Decisions for Ford adaptation that should be made with stakeholders:

1. **GitHub App vs PAT** — App recommended, needs org admin approval
2. **GKE vs Cloud Run** — GKE if existing cluster, Cloud Run if greenfield
3. **Shared vs dedicated VPC** — depends on network team policy
4. **BigQuery dataset location** — US multi-region vs specific region (compliance)
5. **Slack workspace** — which workspace, who gets access
6. **First pilot team** — ideally one with both GHA and Tekton for full coverage
7. **DORA metrics visibility** — team-only or leadership dashboard
