"""Tests for governance scripts and documentation."""

from __future__ import annotations

import os
import re
import subprocess

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestGovernanceFilesExist:
    def test_governance_setup_script_exists(self):
        path = os.path.join(ROOT, "scripts", "governance-setup.sh")
        assert os.path.isfile(path)

    def test_governance_setup_script_executable(self):
        path = os.path.join(ROOT, "scripts", "governance-setup.sh")
        assert os.access(path, os.X_OK)

    def test_policy_check_script_exists(self):
        path = os.path.join(ROOT, "scripts", "policy-check.sh")
        assert os.path.isfile(path)

    def test_policy_check_script_executable(self):
        path = os.path.join(ROOT, "scripts", "policy-check.sh")
        assert os.access(path, os.X_OK)

    def test_codeowners_exists(self):
        path = os.path.join(ROOT, "CODEOWNERS")
        assert os.path.isfile(path)

    def test_governance_doc_exists(self):
        path = os.path.join(ROOT, "docs", "governance.md")
        assert os.path.isfile(path)

    def test_cost_analysis_doc_exists(self):
        path = os.path.join(ROOT, "docs", "cost-analysis.md")
        assert os.path.isfile(path)

    def test_ford_scale_guide_exists(self):
        path = os.path.join(ROOT, "docs", "ford-scale-guide.md")
        assert os.path.isfile(path)


class TestCodeowners:
    @pytest.fixture
    def content(self):
        with open(os.path.join(ROOT, "CODEOWNERS")) as f:
            return f.read()

    def test_has_default_owner(self, content):
        assert re.search(r"^\*\s+@", content, re.MULTILINE)

    def test_has_tools_owner(self, content):
        assert "tools/" in content.lower()

    def test_has_callbacks_owner(self, content):
        assert "callbacks.py" in content

    def test_no_empty_owner_lines(self, content):
        for line in content.strip().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            assert len(parts) >= 2, f"CODEOWNERS line missing owner: {line}"


class TestGovernanceDoc:
    @pytest.fixture
    def content(self):
        with open(os.path.join(ROOT, "docs", "governance.md")) as f:
            return f.read()

    def test_has_branch_protection(self, content):
        assert re.search(r"branch.protect", content, re.IGNORECASE)

    def test_has_secret_scanning(self, content):
        assert re.search(r"secret.scan", content, re.IGNORECASE)

    def test_has_owasp(self, content):
        assert "OWASP" in content

    def test_has_audit(self, content):
        assert re.search(r"audit", content, re.IGNORECASE)

    def test_has_guardrail(self, content):
        assert re.search(r"guardrail", content, re.IGNORECASE)

    def test_has_rate_limiting(self, content):
        assert re.search(r"rate.limit", content, re.IGNORECASE)

    def test_has_destructive_tools(self, content):
        assert "_DESTRUCTIVE_TOOLS" in content

    def test_has_ford_adaptations(self, content):
        assert re.search(r"ford", content, re.IGNORECASE)


class TestGovernanceSetupScript:
    def test_valid_bash_syntax(self):
        path = os.path.join(ROOT, "scripts", "governance-setup.sh")
        result = subprocess.run(
            ["bash", "-n", path], capture_output=True, text=True, timeout=10
        )
        assert result.returncode == 0, f"Syntax errors: {result.stderr}"

    def test_uses_gh_cli(self):
        with open(os.path.join(ROOT, "scripts", "governance-setup.sh")) as f:
            content = f.read()
        assert "gh api" in content

    def test_has_shebang(self):
        with open(os.path.join(ROOT, "scripts", "governance-setup.sh")) as f:
            first_line = f.readline()
        assert first_line.startswith("#!/")

    def test_references_all_repos(self):
        with open(os.path.join(ROOT, "scripts", "governance-setup.sh")) as f:
            content = f.read()
        for repo in ["platform-workflows", "platform-terraform", "platform-agent",
                      "sample-flask-app", "sample-nextjs-app"]:
            assert repo in content, f"Missing repo: {repo}"

    def test_configures_branch_protection(self):
        with open(os.path.join(ROOT, "scripts", "governance-setup.sh")) as f:
            content = f.read()
        assert "branches/main/protection" in content

    def test_enables_secret_scanning(self):
        with open(os.path.join(ROOT, "scripts", "governance-setup.sh")) as f:
            content = f.read()
        assert "secret_scanning" in content


class TestPolicyCheckScript:
    def test_valid_bash_syntax(self):
        path = os.path.join(ROOT, "scripts", "policy-check.sh")
        result = subprocess.run(
            ["bash", "-n", path], capture_output=True, text=True, timeout=10
        )
        assert result.returncode == 0, f"Syntax errors: {result.stderr}"

    def test_has_shebang(self):
        with open(os.path.join(ROOT, "scripts", "policy-check.sh")) as f:
            first_line = f.readline()
        assert first_line.startswith("#!/")

    def test_checks_for_secrets(self):
        with open(os.path.join(ROOT, "scripts", "policy-check.sh")) as f:
            content = f.read()
        assert "SECRET_PATTERNS" in content or "secret" in content.lower()

    def test_checks_banned_patterns(self):
        with open(os.path.join(ROOT, "scripts", "policy-check.sh")) as f:
            content = f.read()
        assert "BANNED_PATTERNS" in content

    def test_validates_guardrails(self):
        with open(os.path.join(ROOT, "scripts", "policy-check.sh")) as f:
            content = f.read()
        assert "before_agent_guardrail" in content

    def test_checks_tool_annotations(self):
        with open(os.path.join(ROOT, "scripts", "policy-check.sh")) as f:
            content = f.read()
        assert "ToolAnnotations" in content

    def test_checks_file_sizes(self):
        with open(os.path.join(ROOT, "scripts", "policy-check.sh")) as f:
            content = f.read()
        assert "MAX_FILE_SIZE" in content


class TestCostAnalysisDoc:
    @pytest.fixture
    def content(self):
        with open(os.path.join(ROOT, "docs", "cost-analysis.md")) as f:
            return f.read()

    def test_has_llm_costs(self, content):
        assert "Gemini" in content and "Claude" in content

    def test_has_infrastructure_costs(self, content):
        assert re.search(r"infrastructure", content, re.IGNORECASE)

    def test_has_roi_analysis(self, content):
        assert "ROI" in content

    def test_has_backstage_comparison(self, content):
        assert "Backstage" in content

    def test_has_ford_scale_projections(self, content):
        assert "80 teams" in content


class TestFordScaleGuide:
    @pytest.fixture
    def content(self):
        with open(os.path.join(ROOT, "docs", "ford-scale-guide.md")) as f:
            return f.read()

    def test_has_ghec_emu(self, content):
        assert "GHEC EMU" in content or "GHEC" in content

    def test_has_vpc_sc(self, content):
        assert "VPC-SC" in content or "VPC Service Controls" in content

    def test_has_workload_identity(self, content):
        assert "Workload Identity" in content

    def test_has_migration_phases(self, content):
        assert "Phase 1" in content and "Phase 2" in content

    def test_has_risk_mitigation(self, content):
        assert re.search(r"risk", content, re.IGNORECASE)
