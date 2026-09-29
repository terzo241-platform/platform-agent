#!/usr/bin/env bash
# policy-check.sh — Pre-merge policy validation.
#
# Run in CI or locally before merging PRs.
# Checks: secrets in code, banned patterns, license compliance, file size limits.
#
# Usage:
#   ./scripts/policy-check.sh [--fix]
#
# Exit codes:
#   0  all checks passed
#   1  policy violations found

set -euo pipefail

FIX_MODE=false
[[ "${1:-}" == "--fix" ]] && FIX_MODE=true

VIOLATIONS=0
WARNINGS=0

log()  { printf '\033[1;34m[policy]\033[0m %s\n' "$*"; }
pass() { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  ⚠\033[0m %s\n' "$*"; WARNINGS=$((WARNINGS+1)); }
fail() { printf '\033[1;31m  ✗\033[0m %s\n' "$*"; VIOLATIONS=$((VIOLATIONS+1)); }

# Determine repo root
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "$REPO_ROOT"

# ---------------------------------------------------------------------------
# 1. Secret detection (lightweight — complements GitHub secret scanning)
# ---------------------------------------------------------------------------
log "Checking for hardcoded secrets"

SECRET_PATTERNS=(
  'AKIA[0-9A-Z]{16}'                    # AWS access key
  'ghp_[a-zA-Z0-9]{36}'                 # GitHub PAT (classic)
  'github_pat_[a-zA-Z0-9_]{82}'         # GitHub PAT (fine-grained)
  'glpat-[a-zA-Z0-9_\-]{20}'            # GitLab PAT
  'sk-[a-zA-Z0-9]{48}'                  # OpenAI API key
  'AIza[0-9A-Za-z_\-]{35}'              # Google API key
  'xoxb-[0-9]{10,13}-[0-9]{10,13}-[a-zA-Z0-9]{24}'  # Slack bot token
)

SCAN_FILES=$(git diff --cached --name-only --diff-filter=ACM 2>/dev/null || find . -type f \
  -not -path './.git/*' \
  -not -path './node_modules/*' \
  -not -path './.venv/*' \
  -not -path './__pycache__/*' \
  -not -name '*.pyc' \
  -not -name '*.whl' \
  -not -name '*.zip' \
  | head -500)

SECRET_FOUND=false
for pattern in "${SECRET_PATTERNS[@]}"; do
  while IFS= read -r match; do
    if [[ -n "$match" ]]; then
      fail "Potential secret found: $match"
      SECRET_FOUND=true
    fi
  done < <(echo "$SCAN_FILES" | xargs grep -rlnE "$pattern" 2>/dev/null || true)
done

if [[ "$SECRET_FOUND" == "false" ]]; then
  pass "No hardcoded secrets detected"
fi

# ---------------------------------------------------------------------------
# 2. Banned patterns (production safety)
# ---------------------------------------------------------------------------
log "Checking for banned patterns"

declare -A BANNED_PATTERNS=(
  ["TODO.*HACK"]="Unresolved hacks should not be merged"
  ["os\.system\("]="Use subprocess.run() instead of os.system()"
  ["eval\("]="eval() is a code injection risk"
  ["exec\("]="exec() is a code injection risk"
  ["__import__\("]="Dynamic imports via __import__ are dangerous"
  ["pickle\.loads?\("]="pickle deserialization is unsafe with untrusted data"
  ["yaml\.load\("]="Use yaml.safe_load() instead of yaml.load()"
  ["shell=True"]="subprocess shell=True enables command injection"
  ["disable_ssl"]="SSL/TLS must not be disabled"
  ["verify=False"]="TLS verification must not be disabled"
)

PY_FILES=$(find . -name '*.py' -not -path './.venv/*' -not -path './__pycache__/*' 2>/dev/null)

for pattern in "${!BANNED_PATTERNS[@]}"; do
  reason="${BANNED_PATTERNS[$pattern]}"
  while IFS= read -r match; do
    if [[ -n "$match" ]]; then
      fail "Banned pattern '$pattern': $match — $reason"
    fi
  done < <(echo "$PY_FILES" | xargs grep -rnE "$pattern" 2>/dev/null | grep -v '# noqa: policy' || true)
done

# Check for files that should never be committed
FORBIDDEN_FILES=(".env" "credentials.json" "service-account.json" "*.pem" "*.key")
for pattern in "${FORBIDDEN_FILES[@]}"; do
  found=$(find . -name "$pattern" -not -path './.git/*' -not -path './.venv/*' 2>/dev/null)
  if [[ -n "$found" ]]; then
    fail "Forbidden file committed: $found"
  fi
done

if [[ $VIOLATIONS -eq 0 ]]; then
  pass "No banned patterns found"
fi

# ---------------------------------------------------------------------------
# 3. File size limits
# ---------------------------------------------------------------------------
log "Checking file sizes"

MAX_FILE_SIZE_KB=1024  # 1 MB

while IFS= read -r large_file; do
  if [[ -n "$large_file" ]]; then
    size_kb=$(du -k "$large_file" | cut -f1)
    fail "File too large (${size_kb}KB > ${MAX_FILE_SIZE_KB}KB): $large_file"
  fi
done < <(find . -type f -size +${MAX_FILE_SIZE_KB}k \
  -not -path './.git/*' \
  -not -path './.venv/*' \
  -not -path './node_modules/*' \
  -not -name '*.whl' \
  2>/dev/null)

# ---------------------------------------------------------------------------
# 4. Python-specific checks
# ---------------------------------------------------------------------------
log "Checking Python code quality"

# Check for missing __init__.py in packages
for dir in $(find ford_platform_agent -type d -not -path '*__pycache__*' 2>/dev/null); do
  if [[ ! -f "$dir/__init__.py" ]] && ls "$dir"/*.py >/dev/null 2>&1; then
    warn "Missing __init__.py in $dir"
  fi
done

# Check pyproject.toml has pinned major versions (not ==, but >= with upper bound or ~=)
if [[ -f pyproject.toml ]]; then
  if grep -qE 'dependencies.*\*' pyproject.toml 2>/dev/null; then
    warn "Wildcard dependency version found in pyproject.toml"
  else
    pass "Dependencies have version constraints"
  fi
fi

# ---------------------------------------------------------------------------
# 5. Agent-specific guardrail validation
# ---------------------------------------------------------------------------
log "Checking agent guardrails"

# Verify callbacks.py defines required guardrails
if [[ -f ford_platform_agent/callbacks.py ]]; then
  if grep -q 'before_agent_guardrail' ford_platform_agent/callbacks.py; then
    pass "before_agent_guardrail defined in callbacks.py"
  else
    fail "Missing before_agent_guardrail in callbacks.py"
  fi

  if grep -q '_DESTRUCTIVE_TOOLS' ford_platform_agent/callbacks.py; then
    pass "Destructive tools set defined"
  else
    fail "Missing _DESTRUCTIVE_TOOLS definition in callbacks.py"
  fi

  if grep -q '_PROD_BLOCKED_TOOLS' ford_platform_agent/callbacks.py; then
    pass "Production-blocked tools set defined"
  else
    fail "Missing _PROD_BLOCKED_TOOLS definition in callbacks.py"
  fi
fi

# Verify agent.py uses guardrail callbacks
if [[ -f ford_platform_agent/agent.py ]]; then
  if grep -q 'before_agent_callback' ford_platform_agent/agent.py; then
    pass "Agent uses before_agent_callback"
  else
    fail "Agent missing before_agent_callback — guardrails bypassed!"
  fi

  if grep -q 'require_confirmation' ford_platform_agent/agent.py; then
    pass "Write tools require confirmation"
  else
    fail "Write tools missing require_confirmation — unsafe!"
  fi
fi

# Verify MCP server has tool annotations
if [[ -f ford_platform_agent/mcp_server.py ]]; then
  annotation_count=$(grep -c 'ToolAnnotations' ford_platform_agent/mcp_server.py 2>/dev/null || echo 0)
  if [[ $annotation_count -gt 0 ]]; then
    pass "MCP server has $annotation_count tool annotations (read-only/destructive markers)"
  else
    warn "MCP server missing ToolAnnotations — clients can't distinguish read/write tools"
  fi
fi

# ---------------------------------------------------------------------------
# 6. Summary
# ---------------------------------------------------------------------------
echo ""
log "════════════════════════════════════════"
log "Policy Check Complete"
log "════════════════════════════════════════"
log "  Violations:  $VIOLATIONS"
log "  Warnings:    $WARNINGS"
echo ""

if [[ $VIOLATIONS -gt 0 ]]; then
  fail "BLOCKED: $VIOLATIONS policy violation(s) must be resolved before merge"
  exit 1
fi

if [[ $WARNINGS -gt 0 ]]; then
  warn "$WARNINGS warning(s) — review recommended but not blocking"
fi

pass "All policy checks passed"
