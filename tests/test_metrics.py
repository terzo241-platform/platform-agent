"""Tests for DORA metrics engine — calculator logic, collector mocking, and tool functions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx

from ford_platform_agent.tools.metrics import (
    _REWORK_PATTERNS,
    MetricsCalculator,
    MetricsCollector,
    _generate_recommendations,
    _is_deploy_workflow,
    compare_repos,
    get_dora_metrics,
    get_metric_trends,
    get_team_metrics,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_deployment(
    hours_ago: float,
    status: str = "success",
    env: str = "production",
    deploy_id: int = 1,
) -> dict:
    created = datetime.now(UTC) - timedelta(hours=hours_ago)
    return {
        "source": "deployment_api",
        "id": str(deploy_id),
        "created_at": created.isoformat(),
        "environment": env,
        "sha": f"abc{deploy_id:04d}",
        "status": status,
        "creator": "bot",
    }


def _make_pr(
    hours_ago_created: float,
    hours_ago_merged: float,
    title: str = "Add feature",
    number: int = 1,
) -> dict:
    created = datetime.now(UTC) - timedelta(hours=hours_ago_created)
    merged = datetime.now(UTC) - timedelta(hours=hours_ago_merged)
    return {
        "number": number,
        "title": title,
        "created_at": created.isoformat(),
        "merged_at": merged.isoformat(),
        "author": "dev",
        "head_sha": f"sha{number:04d}",
        "labels": [],
        "additions": 50,
        "deletions": 10,
    }


# ---------------------------------------------------------------------------
# Deployment Frequency
# ---------------------------------------------------------------------------


class TestDeploymentFrequency:
    def test_zero_deployments(self):
        result = MetricsCalculator.deployment_frequency([], 30)
        assert result["value"] == 0
        assert result["grade"] == "Low"
        assert result["total_deployments"] == 0

    def test_single_deployment_in_30_days(self):
        deps = [_make_deployment(hours_ago=100)]
        result = MetricsCalculator.deployment_frequency(deps, 30)
        assert result["total_deployments"] == 1
        assert result["grade"] == "Medium"  # 30/1 = 30 avg days between = monthly boundary

    def test_daily_deployments_is_high(self):
        deps = [_make_deployment(hours_ago=i * 48, deploy_id=i) for i in range(3)]
        result = MetricsCalculator.deployment_frequency(deps, 14)
        assert result["grade"] == "High"  # 14/3 ≈ 4.7 avg days between = daily to weekly

    def test_multiple_per_day_is_elite(self):
        deps = [_make_deployment(hours_ago=i * 4, deploy_id=i) for i in range(30)]
        result = MetricsCalculator.deployment_frequency(deps, 5)
        assert result["grade"] == "Elite"

    def test_weekly_is_medium(self):
        deps = [_make_deployment(hours_ago=i * 168, deploy_id=i) for i in range(3)]
        result = MetricsCalculator.deployment_frequency(deps, 30)
        assert result["grade"] == "Medium"

    def test_value_is_deploys_per_day(self):
        deps = [_make_deployment(hours_ago=i * 12, deploy_id=i) for i in range(10)]
        result = MetricsCalculator.deployment_frequency(deps, 10)
        assert result["value"] == 1.0
        assert result["unit"] == "deploys/day"

    def test_period_days_stored(self):
        result = MetricsCalculator.deployment_frequency([], 90)
        assert result["period_days"] == 90


# ---------------------------------------------------------------------------
# Lead Time for Changes
# ---------------------------------------------------------------------------


class TestLeadTime:
    def test_no_prs(self):
        result = MetricsCalculator.lead_time_for_changes([], [])
        assert result["value"] == 0
        assert result["grade"] == "Low"
        assert result["sample_size"] == 0

    def test_instant_merge_is_elite(self):
        prs = [_make_pr(hours_ago_created=0.5, hours_ago_merged=0.1)]
        result = MetricsCalculator.lead_time_for_changes(prs, [])
        assert result["grade"] == "Elite"
        assert result["value"] <= 1

    def test_hours_is_high(self):
        prs = [_make_pr(hours_ago_created=20, hours_ago_merged=8)]
        result = MetricsCalculator.lead_time_for_changes(prs, [])
        assert result["grade"] == "High"

    def test_days_is_medium(self):
        prs = [_make_pr(hours_ago_created=100, hours_ago_merged=10)]
        result = MetricsCalculator.lead_time_for_changes(prs, [])
        assert result["grade"] == "Medium"

    def test_weeks_is_low(self):
        prs = [_make_pr(hours_ago_created=500, hours_ago_merged=10)]
        result = MetricsCalculator.lead_time_for_changes(prs, [])
        assert result["grade"] == "Low"

    def test_median_with_multiple_prs(self):
        prs = [
            _make_pr(hours_ago_created=2, hours_ago_merged=1.5, number=1),
            _make_pr(hours_ago_created=100, hours_ago_merged=50, number=2),
            _make_pr(hours_ago_created=3, hours_ago_merged=2.5, number=3),
        ]
        result = MetricsCalculator.lead_time_for_changes(prs, [])
        assert result["sample_size"] == 3

    def test_uses_deploy_time_when_available(self):
        prs = [_make_pr(hours_ago_created=10, hours_ago_merged=8, number=1)]
        deploy_at = (datetime.now(UTC) - timedelta(hours=5)).isoformat()
        deps = [{"sha": "sha0001", "created_at": deploy_at}]
        result = MetricsCalculator.lead_time_for_changes(prs, deps)
        assert result["value"] == pytest.approx(5, abs=0.2)


# ---------------------------------------------------------------------------
# Change Failure Rate
# ---------------------------------------------------------------------------


class TestChangeFailureRate:
    def test_no_deployments(self):
        result = MetricsCalculator.change_failure_rate([])
        assert result["value"] == 0
        assert result["grade"] == "Low"

    def test_zero_failures_is_elite(self):
        deps = [_make_deployment(hours_ago=i, deploy_id=i) for i in range(20)]
        result = MetricsCalculator.change_failure_rate(deps)
        assert result["value"] == 0
        assert result["grade"] == "Elite"

    def test_all_failures_is_low(self):
        deps = [_make_deployment(hours_ago=i, status="failure", deploy_id=i) for i in range(10)]
        result = MetricsCalculator.change_failure_rate(deps)
        assert result["value"] == 100
        assert result["grade"] == "Low"

    def test_five_percent_boundary_is_elite(self):
        deps = [_make_deployment(hours_ago=i, deploy_id=i) for i in range(20)]
        deps[0] = _make_deployment(hours_ago=0, status="failure", deploy_id=100)
        result = MetricsCalculator.change_failure_rate(deps)
        assert result["grade"] == "Elite"

    def test_ten_percent_is_high(self):
        deps = [_make_deployment(hours_ago=i, deploy_id=i) for i in range(20)]
        deps[0] = _make_deployment(hours_ago=0, status="failure", deploy_id=100)
        deps[1] = _make_deployment(hours_ago=1, status="failure", deploy_id=101)
        result = MetricsCalculator.change_failure_rate(deps)
        assert result["grade"] == "High"

    def test_error_status_counts_as_failure(self):
        deps = [
            _make_deployment(hours_ago=1, status="success", deploy_id=1),
            _make_deployment(hours_ago=2, status="error", deploy_id=2),
        ]
        result = MetricsCalculator.change_failure_rate(deps)
        assert result["failed_deployments"] == 1

    def test_counts_tracked(self):
        deps = [
            _make_deployment(hours_ago=1, status="success", deploy_id=1),
            _make_deployment(hours_ago=2, status="failure", deploy_id=2),
            _make_deployment(hours_ago=3, status="success", deploy_id=3),
        ]
        result = MetricsCalculator.change_failure_rate(deps)
        assert result["total_deployments"] == 3
        assert result["failed_deployments"] == 1


# ---------------------------------------------------------------------------
# Mean Time to Recovery
# ---------------------------------------------------------------------------


class TestMTTR:
    def test_no_deployments(self):
        result = MetricsCalculator.mean_time_to_recovery([])
        assert result["value"] == 0
        assert result["grade"] == "Low"

    def test_no_failures_is_elite(self):
        deps = [_make_deployment(hours_ago=i, deploy_id=i) for i in range(5)]
        result = MetricsCalculator.mean_time_to_recovery(deps)
        assert result["grade"] == "Elite"
        assert result["incidents"] == 0

    def test_quick_recovery_is_elite(self):
        deps = [
            _make_deployment(hours_ago=3, status="failure", deploy_id=1),
            _make_deployment(hours_ago=2.5, status="success", deploy_id=2),
        ]
        result = MetricsCalculator.mean_time_to_recovery(deps)
        assert result["grade"] == "Elite"
        assert result["value"] == pytest.approx(0.5, abs=0.1)
        assert result["incidents"] == 1

    def test_day_recovery_is_high(self):
        deps = [
            _make_deployment(hours_ago=30, status="failure", deploy_id=1),
            _make_deployment(hours_ago=10, status="success", deploy_id=2),
        ]
        result = MetricsCalculator.mean_time_to_recovery(deps)
        assert result["grade"] == "High"

    def test_week_recovery_is_medium(self):
        deps = [
            _make_deployment(hours_ago=200, status="failure", deploy_id=1),
            _make_deployment(hours_ago=100, status="success", deploy_id=2),
        ]
        result = MetricsCalculator.mean_time_to_recovery(deps)
        assert result["grade"] == "Medium"

    def test_multiple_incidents(self):
        deps = [
            _make_deployment(hours_ago=50, status="failure", deploy_id=1),
            _make_deployment(hours_ago=49, status="success", deploy_id=2),
            _make_deployment(hours_ago=20, status="failure", deploy_id=3),
            _make_deployment(hours_ago=19, status="success", deploy_id=4),
        ]
        result = MetricsCalculator.mean_time_to_recovery(deps)
        assert result["incidents"] == 2
        assert result["value"] == pytest.approx(1.0, abs=0.1)

    def test_failure_without_recovery(self):
        deps = [
            _make_deployment(hours_ago=5, status="failure", deploy_id=1),
        ]
        result = MetricsCalculator.mean_time_to_recovery(deps)
        assert result["incidents"] == 0
        assert result["grade"] == "Elite"


# ---------------------------------------------------------------------------
# AI Rework Rate
# ---------------------------------------------------------------------------


class TestAIReworkRate:
    def test_no_prs(self):
        result = MetricsCalculator.ai_rework_rate([])
        assert result["value"] == 0
        assert result["grade"] == "info"

    def test_no_rework(self):
        prs = [_make_pr(10, 5, title="Add user profile page")]
        result = MetricsCalculator.ai_rework_rate(prs)
        assert result["value"] == 0
        assert result["rework_prs"] == 0

    def test_revert_detected(self):
        prs = [
            _make_pr(10, 5, title="Add feature", number=1),
            _make_pr(8, 4, title="Revert Add feature", number=2),
        ]
        result = MetricsCalculator.ai_rework_rate(prs)
        assert result["rework_prs"] == 1
        assert result["value"] == 50.0

    def test_hotfix_detected(self):
        prs = [_make_pr(10, 5, title="hotfix: patch login flow")]
        result = MetricsCalculator.ai_rework_rate(prs)
        assert result["rework_prs"] == 1

    def test_fix_issue_detected(self):
        prs = [_make_pr(10, 5, title="fix #123 broken endpoint")]
        result = MetricsCalculator.ai_rework_rate(prs)
        assert result["rework_prs"] == 1

    def test_fix_regression_detected(self):
        prs = [_make_pr(10, 5, title="fix regression in auth flow")]
        result = MetricsCalculator.ai_rework_rate(prs)
        assert result["rework_prs"] == 1

    def test_normal_fix_not_false_positive(self):
        prs = [_make_pr(10, 5, title="Add fixture for tests")]
        result = MetricsCalculator.ai_rework_rate(prs)
        assert result["rework_prs"] == 0

    def test_total_prs_tracked(self):
        prs = [_make_pr(i, i - 1, number=i) for i in range(1, 11)]
        result = MetricsCalculator.ai_rework_rate(prs)
        assert result["total_prs"] == 10


# ---------------------------------------------------------------------------
# Overall Score
# ---------------------------------------------------------------------------


class TestOverallScore:
    def test_all_elite(self):
        metrics = {
            "deployment_frequency": {"grade": "Elite"},
            "lead_time": {"grade": "Elite"},
            "change_failure_rate": {"grade": "Elite"},
            "mttr": {"grade": "Elite"},
        }
        result = MetricsCalculator.overall_score(metrics)
        assert result["score"] == 100
        assert result["grade"] == "A"
        assert result["level"] == "Elite"

    def test_all_low(self):
        metrics = {
            "deployment_frequency": {"grade": "Low"},
            "lead_time": {"grade": "Low"},
            "change_failure_rate": {"grade": "Low"},
            "mttr": {"grade": "Low"},
        }
        result = MetricsCalculator.overall_score(metrics)
        assert result["score"] == 25
        assert result["grade"] == "D"
        assert result["level"] == "Low"

    def test_mixed_grades(self):
        metrics = {
            "deployment_frequency": {"grade": "Elite"},
            "lead_time": {"grade": "High"},
            "change_failure_rate": {"grade": "Medium"},
            "mttr": {"grade": "Low"},
        }
        result = MetricsCalculator.overall_score(metrics)
        assert result["score"] == 62
        assert result["grade"] == "C"

    def test_boundary_90_is_a(self):
        metrics = {
            "deployment_frequency": {"grade": "Elite"},
            "lead_time": {"grade": "Elite"},
            "change_failure_rate": {"grade": "Elite"},
            "mttr": {"grade": "High"},
        }
        result = MetricsCalculator.overall_score(metrics)
        assert result["score"] == 94
        assert result["grade"] == "A"

    def test_boundary_75_is_b(self):
        metrics = {
            "deployment_frequency": {"grade": "High"},
            "lead_time": {"grade": "High"},
            "change_failure_rate": {"grade": "High"},
            "mttr": {"grade": "High"},
        }
        result = MetricsCalculator.overall_score(metrics)
        assert result["score"] == 75
        assert result["grade"] == "B"


# ---------------------------------------------------------------------------
# Rework Pattern Regex
# ---------------------------------------------------------------------------


class TestReworkPatterns:
    @pytest.mark.parametrize(
        "title",
        [
            "Revert 'Add new endpoint'",
            "revert changes from PR #45",
            "hotfix: login broken",
            "hot-fix session timeout",
            "fix #99 broken pagination",
            "fixes #12 null pointer",
            "fixed bug in auth",
            "fixing regression in parser",
            "rollback deployment v2.3",
        ],
    )
    def test_matches_rework(self, title: str):
        assert _REWORK_PATTERNS.search(title) is not None

    @pytest.mark.parametrize(
        "title",
        [
            "Add new feature",
            "Update documentation",
            "Refactor user service",
            "Add fixture for tests",
            "Prefix suffix handling",
        ],
    )
    def test_does_not_match_normal(self, title: str):
        assert _REWORK_PATTERNS.search(title) is None


# ---------------------------------------------------------------------------
# Deploy Workflow Detection
# ---------------------------------------------------------------------------


class TestDeployWorkflowDetection:
    def test_deploy_in_name(self):
        assert _is_deploy_workflow("Deploy to production", "") is True

    def test_deploy_in_path(self):
        assert _is_deploy_workflow("CI", ".github/workflows/deploy.yml") is True

    def test_release_detected(self):
        assert _is_deploy_workflow("Release v2.0", "") is True

    def test_ci_not_deploy(self):
        assert _is_deploy_workflow("CI", ".github/workflows/ci.yml") is False

    def test_promote_detected(self):
        assert _is_deploy_workflow("Promote to staging", "") is True


# ---------------------------------------------------------------------------
# Recommendations
# ---------------------------------------------------------------------------


class TestRecommendations:
    def test_low_df_gets_recommendations(self):
        metrics = {"deployment_frequency": {"grade": "Low"}}
        recs = _generate_recommendations(metrics)
        assert len(recs) >= 2

    def test_elite_gets_no_recommendations(self):
        metrics = {
            "deployment_frequency": {"grade": "Elite"},
            "lead_time": {"grade": "Elite"},
            "change_failure_rate": {"grade": "Elite"},
            "mttr": {"grade": "Elite"},
        }
        recs = _generate_recommendations(metrics)
        assert len(recs) == 0

    def test_multiple_weak_areas(self):
        metrics = {
            "deployment_frequency": {"grade": "Low"},
            "lead_time": {"grade": "Low"},
            "change_failure_rate": {"grade": "Elite"},
            "mttr": {"grade": "Elite"},
        }
        recs = _generate_recommendations(metrics)
        assert len(recs) >= 4


# ---------------------------------------------------------------------------
# MetricsCollector (mocked GitHub API)
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_client():
    return httpx.AsyncClient(
        base_url="https://api.github.com",
        headers={"Authorization": "Bearer test"},
    )


class TestMetricsCollector:
    @respx.mock
    @pytest.mark.asyncio
    async def test_collect_deployments_empty(self, mock_client):
        respx.get("https://api.github.com/repos/org/my-app/deployments").mock(
            return_value=httpx.Response(200, json=[])
        )
        respx.get("https://api.github.com/repos/org/my-app/actions/runs").mock(
            return_value=httpx.Response(200, json={"workflow_runs": []})
        )

        collector = MetricsCollector(mock_client, "org")
        result = await collector.collect_deployments("my-app", 30)
        assert result == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_collect_prs_filters_merged_only(self, mock_client):
        now = datetime.now(UTC)
        prs = [
            {
                "number": 1,
                "title": "Merged PR",
                "merged_at": now.isoformat(),
                "created_at": (now - timedelta(hours=2)).isoformat(),
                "user": {"login": "dev"},
                "merge_commit_sha": "abc123",
                "labels": [],
                "additions": 10,
                "deletions": 5,
            },
            {
                "number": 2,
                "title": "Unmerged PR",
                "merged_at": None,
                "created_at": now.isoformat(),
                "user": {"login": "dev"},
                "merge_commit_sha": None,
                "labels": [],
                "additions": 0,
                "deletions": 0,
            },
        ]
        call_count = 0

        def _side_effect(request):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(200, json=prs)
            return httpx.Response(200, json=[])

        respx.get(url__startswith="https://api.github.com/repos/org/my-app/pulls").mock(
            side_effect=_side_effect
        )

        collector = MetricsCollector(mock_client, "org")
        result = await collector.collect_pull_requests("my-app", 30)
        assert len(result) == 1
        assert result[0]["number"] == 1

    @respx.mock
    @pytest.mark.asyncio
    async def test_collect_workflow_runs(self, mock_client):
        runs = {
            "workflow_runs": [
                {
                    "id": 1,
                    "name": "CI",
                    "status": "completed",
                    "conclusion": "success",
                    "created_at": datetime.now(UTC).isoformat(),
                    "updated_at": datetime.now(UTC).isoformat(),
                    "head_branch": "main",
                    "event": "push",
                },
            ]
        }
        respx.get("https://api.github.com/repos/org/my-app/actions/runs").mock(
            return_value=httpx.Response(200, json=runs)
        )

        collector = MetricsCollector(mock_client, "org")
        result = await collector.collect_workflow_runs("my-app", 30)
        assert len(result) == 1
        assert result[0]["name"] == "CI"

    @respx.mock
    @pytest.mark.asyncio
    async def test_rate_limit_handling(self, mock_client):
        respx.get("https://api.github.com/repos/org/my-app/commits").mock(
            return_value=httpx.Response(
                403,
                json={"message": "API rate limit exceeded"},
                headers={"X-RateLimit-Remaining": "0"},
            )
        )

        collector = MetricsCollector(mock_client, "org")
        result = await collector.collect_commits("my-app", 30)
        assert result == []

    @respx.mock
    @pytest.mark.asyncio
    async def test_404_raises(self, mock_client):
        respx.get("https://api.github.com/repos/org/nonexistent/deployments").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )

        collector = MetricsCollector(mock_client, "org")
        with pytest.raises(httpx.HTTPStatusError):
            await collector.collect_deployments("nonexistent", 30)

    @respx.mock
    @pytest.mark.asyncio
    async def test_repo_path_with_org_prefix(self, mock_client):
        respx.get("https://api.github.com/repos/custom-org/app/commits").mock(
            return_value=httpx.Response(200, json=[])
        )

        collector = MetricsCollector(mock_client, "org")
        result = await collector.collect_commits("custom-org/app", 30)
        assert result == []


# ---------------------------------------------------------------------------
# Tool function integration tests
# ---------------------------------------------------------------------------


class TestToolFunctions:
    @pytest.mark.asyncio
    async def test_get_dora_metrics_no_registry(self):
        import ford_platform_agent.tools.metrics as m

        original = m._registry
        m._registry = None
        try:
            result = await get_dora_metrics("test-repo")
            assert result["error"] == "not_initialized"
        finally:
            m._registry = original

    @pytest.mark.asyncio
    async def test_get_team_metrics_empty_input(self):
        result = await get_team_metrics("")
        assert result["error"] == "invalid_input"

    @pytest.mark.asyncio
    async def test_compare_repos_needs_two(self):
        result = await compare_repos("only-one")
        assert result["error"] == "invalid_input"

    @pytest.mark.asyncio
    async def test_get_metric_trends_needs_two_periods(self):
        result = await get_metric_trends("repo", periods=1)
        assert result["error"] == "invalid_input"
