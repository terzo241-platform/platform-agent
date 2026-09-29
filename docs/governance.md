# Governance & Policy Framework

## Overview

Three enforcement layers protect the platform at different points in the lifecycle:

```
Code Commit          Agent Runtime         Production
─────────────       ──────────────        ──────────
CODEOWNERS           Rate limiter          Separation of duties
Branch protection    HITL for write ops    Prod-blocked tools
Secret scanning      Destructive guard     Audit logging
policy-check.sh      Environment guard     OTel tracing (planned)
CodeQL + gitleaks    OWASP LLM Top 10     Immutable logs (planned)
```

## Layer 1: Code Commit (GitHub)

Automated via `scripts/governance-setup.sh`:

| Control | Enforcement | File |
|---|---|---|
| Branch protection | 1 reviewer, no force push, stale dismissal | governance-setup.sh |
| Code ownership | Domain-specific reviewers auto-assigned | CODEOWNERS |
| Secret scanning | GitHub native + push protection | governance-setup.sh |
| Vulnerability alerts | Dependabot security + version updates | governance-setup.sh |
| Policy check | Pre-merge script (secrets, banned patterns, guardrail presence) | policy-check.sh |
| SAST | CodeQL + gitleaks via reusable workflow | platform-workflows |

## Layer 2: Agent Runtime (callbacks.py)

Guardrails enforced server-side in the ADK path, per-tool in the MCP path.

### Rate Limiting

```python
RateLimiter(max_per_min=30)  # sliding window
```

Applied in `before_agent_guardrail()`. Prevents runaway loops or abuse.

### Destructive Tool Guard

```python
_DESTRUCTIVE_TOOLS = {
    "cancel_pipeline",
    "rollback_application",
    "approve_and_merge",
}
```

These tools require explicit human confirmation (`require_confirmation=True` in ADK, `WRITE_DESTRUCTIVE` annotation in MCP).

### Production Blocklist

```python
_PROD_BLOCKED_TOOLS = {
    "scaffold_project",      # never scaffold directly in prod
    "trigger_pipeline",      # CI triggers go through PR flow
}
```

Blocked unconditionally for production environments.

### Environment-Aware Confirmation

```python
requires_confirmation_for_env(env_name)
```

Dynamic confirmation based on environment sensitivity:
- `dev` — no confirmation needed
- `staging` — confirmation for write operations
- `prod` — confirmation for all operations + separation of duties

### Separation of Duties

In `tools/infra.py`, `approve_and_merge()` enforces:
- PR author cannot approve their own PR
- Approval requires a different identity than the creator

## Layer 3: MCP Tool Annotations

Every MCP tool is annotated with safety metadata:

```python
@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=True,     # READ_ONLY: safe to call without confirmation
))

@mcp.tool(annotations=ToolAnnotations(
    readOnlyHint=False,    # WRITE_SAFE: modifies state, confirm recommended
))

@mcp.tool(annotations=ToolAnnotations(
    destructiveHint=True,  # WRITE_DESTRUCTIVE: requires explicit approval
))
```

MCP-compatible clients (Claude Code, VS Code) use these annotations to decide when to prompt for confirmation.

## OWASP LLM Top 10 (2025) Coverage

| # | Risk | Mitigation | Status |
|---|---|---|---|
| LLM01 | Prompt Injection | System instruction guardrails, tool-level input validation | Built |
| LLM02 | Insecure Output Handling | Structured tool outputs (dicts, not raw strings) | Built |
| LLM03 | Training Data Poisoning | N/A (using managed LLM APIs, not fine-tuned models) | N/A |
| LLM04 | Model Denial of Service | Rate limiter (30 req/min), async non-blocking tools | Built |
| LLM05 | Supply Chain Vulnerabilities | Dependabot, CodeQL, pinned dependencies, SBOM | Built |
| LLM06 | Sensitive Info Disclosure | No secrets in tool outputs, audit logging, secret scanning | Built |
| LLM07 | Insecure Plugin Design | Tool annotations, require_confirmation, separation of duties | Built |
| LLM08 | Excessive Agency | HITL for writes, prod blocklist, environment guards | Built |
| LLM09 | Overreliance | DORA metrics grounded in data, agent cites sources | Built |
| LLM10 | Model Theft | Managed API (Vertex AI), no model weights stored | N/A |

## Audit Strategy

### Current (POC)

```python
# after_agent_audit() in callbacks.py
structlog.get_logger().info(
    "tool_invocation",
    tool=tool_name,
    args=sanitized_args,
    outcome=result_status,
    user=identity,
    timestamp=utc_now,
)
```

### Production Roadmap

| Phase | Sink | Retention | Query |
|---|---|---|---|
| POC | structlog → stdout | Container logs (7 days) | `kubectl logs` |
| Pilot | Cloud Logging | 30 days | Log Analytics |
| Production | BigQuery | 90 days + cold storage | SQL, dashboards |

### Planned: OpenTelemetry Integration

ADK 1.17.0+ has native OTel support:

```python
# Planned: enable in agent.py
from google.adk.telemetry import enable_telemetry
enable_telemetry(
    service_name="ford-platform-agent",
    exporter="cloud_trace",
)
```

Traces will capture: LLM call latency, tool execution time, guardrail decisions, session lifecycle.

## Running Governance Checks

### One-time org setup

```bash
GITHUB_ORG=terzo241-platform ./scripts/governance-setup.sh
```

### Pre-merge policy check (CI or local)

```bash
./scripts/policy-check.sh
```

### In CI (GitHub Actions)

```yaml
- name: Policy Check
  run: ./scripts/policy-check.sh
```

## Ford-Specific Adaptations (Production)

| Requirement | Adaptation |
|---|---|
| GHEC EMU | SAML SSO identity flows into audit `user` field |
| VPC-SC | Agent runs inside perimeter, GitHub API via egress rule |
| Air-gapped providers | Provider mirror in Artifact Registry, `.terraformrc` plugin-cache |
| SOX compliance | Immutable BigQuery audit, 7-year retention for financial systems |
| FedRAMP (if applicable) | Cloud Logging in US region, CMEK encryption at rest |
