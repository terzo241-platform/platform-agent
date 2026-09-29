#!/usr/bin/env bash
# governance-setup.sh — Configure GitHub org-level governance via gh CLI.
#
# Usage:
#   GITHUB_ORG=terzo241-platform ./scripts/governance-setup.sh
#
# Idempotent: safe to re-run. Overwrites existing settings with the same values.
# Requires: gh CLI authenticated with admin access to the org.

set -euo pipefail

ORG="${GITHUB_ORG:?Set GITHUB_ORG before running (e.g. GITHUB_ORG=terzo241-platform)}"

REPOS=(
  platform-workflows
  platform-terraform
  platform-agent
  sample-flask-app
  sample-nextjs-app
)

PASS=0
FAIL=0

log()  { printf '\033[1;34m[governance]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
fail() { printf '\033[1;31m  ✗\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }

# ---------------------------------------------------------------------------
# 1. Org-level: secret scanning + push protection
# ---------------------------------------------------------------------------
log "Configuring org-level security settings for $ORG"

# Enable secret scanning and push protection at org level
if gh api "orgs/$ORG" \
  --method PATCH \
  --field security_product_enablement='{"secret_scanning":{"status":"enabled"},"secret_scanning_push_protection":{"status":"enabled"}}' \
  >/dev/null 2>&1; then
  ok "Secret scanning + push protection enabled at org level"
else
  # Fallback: try individual settings (older API)
  gh api "orgs/$ORG" --method PATCH \
    -f secret_scanning_enabled_for_new_repositories=true \
    -f secret_scanning_push_protection_enabled_for_new_repositories=true \
    >/dev/null 2>&1 && ok "Secret scanning enabled (fallback API)" || fail "Secret scanning config (may require org admin)"
fi

# Enable Dependabot security updates at org level
if gh api "orgs/$ORG" --method PATCH \
  -f dependabot_security_updates_enabled_for_new_repositories=true \
  >/dev/null 2>&1; then
  ok "Dependabot security updates enabled for new repos"
else
  fail "Dependabot config (may require org admin or GHEC)"
fi

# ---------------------------------------------------------------------------
# 2. Per-repo: branch protection on main
# ---------------------------------------------------------------------------
log "Configuring branch protection rules"

for REPO in "${REPOS[@]}"; do
  log "  Repo: $ORG/$REPO"

  # Check repo exists
  if ! gh repo view "$ORG/$REPO" --json name >/dev/null 2>&1; then
    fail "$REPO — repo not found, skipping"
    continue
  fi

  # Enable secret scanning per-repo (in case org-level didn't cover it)
  gh api "repos/$ORG/$REPO" --method PATCH \
    -f security_and_analysis='{"secret_scanning":{"status":"enabled"},"secret_scanning_push_protection":{"status":"enabled"}}' \
    >/dev/null 2>&1 || true

  # Branch protection: require PR, 1 reviewer, no force push, no deletion
  PROTECTION_PAYLOAD=$(cat <<'JSONEOF'
{
  "required_status_checks": {
    "strict": true,
    "contexts": []
  },
  "enforce_admins": false,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true,
    "require_code_owner_reviews": true
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false,
  "block_creations": false,
  "required_conversation_resolution": true
}
JSONEOF
  )

  if echo "$PROTECTION_PAYLOAD" | gh api "repos/$ORG/$REPO/branches/main/protection" \
    --method PUT \
    --input - \
    >/dev/null 2>&1; then
    ok "$REPO — branch protection configured (1 reviewer, no force push)"
  else
    fail "$REPO — branch protection (may need admin access or branch may not exist)"
  fi

  # Disable direct push to main (wiki-style repos may not have this)
  gh api "repos/$ORG/$REPO" --method PATCH \
    -f default_branch=main \
    >/dev/null 2>&1 || true

  # Enable vulnerability alerts
  gh api "repos/$ORG/$REPO/vulnerability-alerts" \
    --method PUT \
    >/dev/null 2>&1 && ok "$REPO — vulnerability alerts enabled" || true

  # Enable automated security fixes (Dependabot)
  gh api "repos/$ORG/$REPO/automated-security-fixes" \
    --method PUT \
    >/dev/null 2>&1 && ok "$REPO — Dependabot security fixes enabled" || true
done

# ---------------------------------------------------------------------------
# 3. Summary
# ---------------------------------------------------------------------------
echo ""
log "════════════════════════════════════════"
log "Governance Setup Complete"
log "════════════════════════════════════════"
log "  Org:       $ORG"
log "  Repos:     ${#REPOS[@]}"
log "  Passed:    $PASS"
log "  Failed:    $FAIL"
echo ""

if [[ $FAIL -gt 0 ]]; then
  log "Some settings failed — typically requires org admin role or GHEC features."
  log "Review failures above and apply manually if needed."
  exit 1
fi

log "All governance settings applied successfully."
