# Platform Agent Demo Script

> Step-by-step demo for leadership presentation.
> Total time: ~15 minutes. Adjust by skipping sections.

## Prerequisites

```bash
# 1. GitHub Personal Access Token (classic, repo + read:org scope)
export GITHUB_TOKEN="ghp_..."
export GITHUB_ORG="terzo241-platform"

# 2. Install the agent
cd platform-agent
pip install -e .

# 3. Verify
ford-agent providers
# Should show: github (CI/CD, SCM) — configured ✓
```

## Demo Flow

### Act 1: "What do I have?" (2 min) — Read-Only, Zero Risk

These queries demonstrate the agent reading data. Nothing is modified.

```bash
# List all repos in the org
ford-agent run "list my repositories"

# Get details on a specific repo
ford-agent run "show me info about sample-flask-app"

# Check CI pipeline status
ford-agent run "what's the status of the last pipeline run on sample-flask-app?"

# List open pull requests
ford-agent run "are there any open PRs on platform-agent?"
```

**Talking point**: "The agent understands natural language and routes to the right API. These are the same tools available in Slack, Chat, and IDE."

### Act 2: "How are we doing?" (3 min) — DORA Metrics

```bash
# Get DORA metrics for a repo
ford-agent run "show me DORA metrics for sample-flask-app over the last 90 days"

# Compare two repos
ford-agent run "compare DORA metrics between sample-flask-app and platform-agent"

# Get recommendations
ford-agent run "give me recommendations to improve sample-flask-app's deployment velocity"

# Trend analysis
ford-agent run "show me the deployment frequency trend for platform-agent over 6 months"
```

**Talking point**: "No vendor dependency — we calculate DORA from GitHub's own API. This works on Day 1 with zero setup for any team."

### Act 3: "Build me something" (5 min) — The Wow Moment

```bash
# Show available templates
ford-agent run "what project templates are available?"

# Scaffold a new service (this creates a real GitHub repo!)
ford-agent run "create a new Python FastAPI service called demo-payments-api"
```

What happens:
1. Creates GitHub repo `terzo241-platform/demo-payments-api`
2. Pushes complete project structure (app code, Dockerfile, tests, CI workflow)
3. Opens a Terraform PR on platform-terraform for Cloud Run infrastructure
4. CI runs automatically on the new repo

**Talking point**: "From 'I need a service' to running CI in under 60 seconds. Today this takes 2-5 days with tickets, templates, and manual setup."

### Act 4: "Same tools, every surface" (3 min) — MCP Demo

```bash
# Add to Claude Code (if Claude Code is available)
claude mcp add ford-platform-agent -- ford-agent mcp

# Then in Claude Code, just ask:
# "what are my DORA metrics for sample-flask-app?"
# "create a new Node.js service called demo-notification-svc"
```

**Talking point**: "The developer doesn't learn a new tool. They use their IDE, and the platform is just... there."

### Act 5: "Governance built in" (2 min) — Trust Layer

```bash
# Run policy check
./scripts/policy-check.sh

# Show guardrails
ford-agent run "approve and merge PR #1 on sample-flask-app"
# → Should show: "Production action requires confirmation"

# Show CODEOWNERS
cat CODEOWNERS
```

**Talking point**: "Every write operation requires human approval. Every action is audited. The agent creates PRs — humans merge them."

## Cleanup

```bash
# Delete demo repo (optional)
gh repo delete terzo241-platform/demo-payments-api --yes
```

## Backup: Test Suite Demo

If GitHub API is unavailable or tokens aren't set up:

```bash
# Run the full test suite — shows everything works
pytest -v --tb=short

# 317 tests, all passing
# Each test demonstrates a tool function with mocked APIs
```

## Key Slides to Reference

| During | Show Slide |
|---|---|
| Before demo | Slide 4: "Proof of Concept: Built to Learn" |
| During DORA | Slide 5: numbers that prove value |
| After scaffold | Slide 8: "How It Works — Request Flow" |
| During governance | Slide 9: guardrails and separation of duties |
| Closing | Slide 11: "What We're Asking For" |

## Anticipated Questions

**Q: "What if the AI gets it wrong?"**
A: The agent can only call registered tools — it can't run arbitrary code. Write operations go through PRs with human review. Wrong tool call = wrong data displayed, not wrong infrastructure changed.

**Q: "How much does the LLM cost?"**
A: ~$240/month at full Ford scale (80 teams, 4,000 queries/day). That's Gemini Flash for 90% of queries. See cost-analysis.md for breakdown.

**Q: "Why not just use Backstage?"**
A: Backstage is a great product. But it requires 6-10 FTE to maintain, takes 12-18 months to deploy, and has <10% adoption outside Spotify. This approach costs 5-10x less and works through tools developers already use.

**Q: "Is this production-ready?"**
A: No. This is a 20-day POC with 317 tests using mocked APIs. Production readiness requires GHEC EMU integration, VPC-SC, audit logging, load testing — estimated 3 months with 2-3 FTE. See ford-scale-guide.md.

**Q: "What about security?"**
A: Three layers: code-time (branch protection, secret scanning, CODEOWNERS), runtime (rate limiting, HITL for writes, environment guards), production (audit trail, separation of duties). OWASP LLM Top 10 mapped in governance.md.

**Q: "Can it work with Tekton?"**
A: Yes — the provider registry already has a Tekton backend. Same tools, different CI engine. No tool code changes needed.
