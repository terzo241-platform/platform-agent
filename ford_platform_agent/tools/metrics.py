"""DORA metrics engine — enterprise-grade engineering effectiveness measurement.

Collects data from GitHub API, calculates all 4 DORA metrics + AI rework rate,
and grades against industry benchmarks (2024 State of DevOps Report). Supports
per-repo, per-team (multi-repo), and org-level rollups.

Architecture:
  GitHub API → MetricsCollector → MetricsCalculator → DORAReport
                                                          ↓
                                                Agent Tool Functions
"""

from __future__ import annotations

import re
import statistics
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import httpx
import structlog

if TYPE_CHECKING:
    from ford_platform_agent.providers.registry import ProviderRegistry

logger = structlog.get_logger()

_registry: ProviderRegistry | None = None

_DORA_BENCHMARKS = {
    "deployment_frequency": {
        "Elite": {"max_days_between": 1, "label": "multiple per day"},
        "High": {"max_days_between": 7, "label": "daily to weekly"},
        "Medium": {"max_days_between": 30, "label": "weekly to monthly"},
        "Low": {"max_days_between": float("inf"), "label": "monthly or less frequent"},
    },
    "lead_time": {
        "Elite": {"max_hours": 1, "label": "less than 1 hour"},
        "High": {"max_hours": 24, "label": "between 1 hour and 1 day"},
        "Medium": {"max_hours": 168, "label": "between 1 day and 1 week"},
        "Low": {"max_hours": float("inf"), "label": "between 1 week and 1 month"},
    },
    "change_failure_rate": {
        "Elite": {"max_percent": 5, "label": "0-5%"},
        "High": {"max_percent": 10, "label": "5-10%"},
        "Medium": {"max_percent": 15, "label": "10-15%"},
        "Low": {"max_percent": float("inf"), "label": "greater than 15%"},
    },
    "mttr": {
        "Elite": {"max_hours": 1, "label": "less than 1 hour"},
        "High": {"max_hours": 24, "label": "less than 1 day"},
        "Medium": {"max_hours": 168, "label": "less than 1 week"},
        "Low": {"max_hours": float("inf"), "label": "more than 1 week"},
    },
}

_GRADE_WEIGHTS = {
    "deployment_frequency": 0.25,
    "lead_time": 0.25,
    "change_failure_rate": 0.25,
    "mttr": 0.25,
}

_LEVEL_SCORES = {"Elite": 100, "High": 75, "Medium": 50, "Low": 25}

_REWORK_PATTERNS = re.compile(
    r"(?i)\b(revert|reverts|reverting|hotfix|hot-fix|rollback|"
    r"fix(?:es|ed|ing)?\s+#\d+|"
    r"fix(?:es|ed|ing)?\s+(?:bug|issue|regression|broken|breaking))\b"
)


def set_registry(registry: ProviderRegistry) -> None:
    global _registry
    _registry = registry


def _get_registry() -> ProviderRegistry:
    if _registry is None:
        raise RuntimeError("ProviderRegistry not initialized. Call set_registry() first.")
    return _registry


def _parse_dt(val: str | None) -> datetime | None:
    if not val:
        return None
    return datetime.fromisoformat(val.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# MetricsCollector — pulls raw data from GitHub API
# ---------------------------------------------------------------------------


class MetricsCollector:
    """Fetches deployment, PR, workflow, and commit data from GitHub API."""

    def __init__(self, client: httpx.AsyncClient, org: str) -> None:
        self._client = client
        self._org = org

    def _repo_path(self, repo: str) -> str:
        if "/" in repo:
            return f"/repos/{repo}"
        return f"/repos/{self._org}/{repo}"

    async def _paginate(
        self, path: str, params: dict | None = None, max_pages: int = 5
    ) -> list[dict]:
        params = {**(params or {}), "per_page": 100}
        results: list[dict] = []
        for page in range(1, max_pages + 1):
            params["page"] = page
            resp = await self._client.get(path, params=params)

            remaining = resp.headers.get("X-RateLimit-Remaining")
            if remaining and int(remaining) < 10:
                logger.warning("github_rate_limit_low", remaining=remaining)

            if resp.status_code == 403 and "rate limit" in resp.text.lower():
                logger.error("github_rate_limit_exceeded")
                break

            resp.raise_for_status()
            data = resp.json()

            if isinstance(data, list):
                if not data:
                    break
                results.extend(data)
            elif isinstance(data, dict):
                items = (
                    data.get("workflow_runs")
                    or data.get("items")
                    or data.get("deployments")
                    or []
                )
                results.extend(items)
                if len(items) < 100:
                    break
            else:
                break
        return results

    async def collect_deployments(self, repo: str, days: int = 30) -> list[dict]:
        since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        path = f"{self._repo_path(repo)}/deployments"

        deployments = await self._paginate(path, {"environment": "production"})

        deploy_runs = await self._paginate(
            f"{self._repo_path(repo)}/actions/runs",
            {"created": f">={since[:10]}"},
        )
        deploy_workflow_runs = [
            r for r in deploy_runs if _is_deploy_workflow(r.get("name", ""), r.get("path", ""))
        ]

        cutoff = datetime.now(UTC) - timedelta(days=days)
        results = []

        for d in deployments:
            created = _parse_dt(d.get("created_at"))
            if created and created >= cutoff:
                results.append({
                    "source": "deployment_api",
                    "id": str(d["id"]),
                    "created_at": d["created_at"],
                    "environment": d.get("environment", ""),
                    "sha": d.get("sha", ""),
                    "status": "success",
                    "creator": d.get("creator", {}).get("login", ""),
                })

        seen_ids = {r["id"] for r in results}
        for r in deploy_workflow_runs:
            run_id = str(r["id"])
            created = _parse_dt(r.get("created_at"))
            if run_id not in seen_ids and created and created >= cutoff:
                results.append({
                    "source": "workflow_run",
                    "id": run_id,
                    "created_at": r["created_at"],
                    "environment": _extract_environment(r),
                    "sha": r.get("head_sha", ""),
                    "status": r.get("conclusion", "unknown"),
                    "creator": r.get("actor", {}).get("login", ""),
                    "workflow_name": r.get("name", ""),
                })

        results.sort(key=lambda x: x.get("created_at", ""))
        return results

    async def collect_pull_requests(self, repo: str, days: int = 30) -> list[dict]:
        since = datetime.now(UTC) - timedelta(days=days)
        path = f"{self._repo_path(repo)}/pulls"
        params = {"state": "closed", "sort": "updated", "direction": "desc"}
        raw = await self._paginate(path, params)

        results = []
        for pr in raw:
            if not pr.get("merged_at"):
                continue
            merged = _parse_dt(pr["merged_at"])
            if merged and merged >= since:
                results.append({
                    "number": pr["number"],
                    "title": pr.get("title", ""),
                    "merged_at": pr["merged_at"],
                    "created_at": pr.get("created_at", ""),
                    "author": pr.get("user", {}).get("login", ""),
                    "head_sha": pr.get("merge_commit_sha", ""),
                    "labels": [lb.get("name", "") for lb in pr.get("labels", [])],
                    "additions": pr.get("additions", 0),
                    "deletions": pr.get("deletions", 0),
                })
        return results

    async def collect_workflow_runs(self, repo: str, days: int = 30) -> list[dict]:
        since = (datetime.now(UTC) - timedelta(days=days)).isoformat()[:10]
        path = f"{self._repo_path(repo)}/actions/runs"
        raw = await self._paginate(path, {"created": f">={since}"})

        return [
            {
                "id": str(r["id"]),
                "name": r.get("name", ""),
                "status": r.get("status", ""),
                "conclusion": r.get("conclusion", ""),
                "created_at": r.get("created_at", ""),
                "updated_at": r.get("updated_at", ""),
                "head_branch": r.get("head_branch", ""),
                "event": r.get("event", ""),
            }
            for r in raw
        ]

    async def collect_commits(self, repo: str, days: int = 30) -> list[dict]:
        since = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        path = f"{self._repo_path(repo)}/commits"
        raw = await self._paginate(path, {"since": since})

        return [
            {
                "sha": c.get("sha", ""),
                "message": c.get("commit", {}).get("message", ""),
                "author": c.get("commit", {}).get("author", {}).get("name", ""),
                "date": c.get("commit", {}).get("author", {}).get("date", ""),
            }
            for c in raw
        ]


def _is_deploy_workflow(name: str, path: str) -> bool:
    name_lower = name.lower()
    path_lower = path.lower()
    deploy_terms = ("deploy", "release", "promote", "publish", "rollout", "cd")
    return any(term in name_lower or term in path_lower for term in deploy_terms)


def _extract_environment(run: dict) -> str:
    name = run.get("name", "").lower()
    branch = run.get("head_branch", "").lower()
    for env in ("production", "prod", "staging", "stg", "dev", "development"):
        if env in name or env in branch:
            return env
    return "unknown"


# ---------------------------------------------------------------------------
# MetricsCalculator — pure logic, no API calls
# ---------------------------------------------------------------------------


class MetricsCalculator:
    """Calculates DORA metrics from collected raw data."""

    @staticmethod
    def deployment_frequency(deployments: list[dict], days: int) -> dict:
        total = len(deployments)
        if total == 0:
            return {
                "value": 0,
                "unit": "deploys/day",
                "grade": "Low",
                "benchmark": _DORA_BENCHMARKS["deployment_frequency"]["Low"]["label"],
                "total_deployments": 0,
                "period_days": days,
            }

        freq = total / max(days, 1)
        avg_days_between = days / total

        if avg_days_between <= 1:
            grade = "Elite"
        elif avg_days_between <= 7:
            grade = "High"
        elif avg_days_between <= 30:
            grade = "Medium"
        else:
            grade = "Low"

        return {
            "value": round(freq, 2),
            "unit": "deploys/day",
            "grade": grade,
            "benchmark": _DORA_BENCHMARKS["deployment_frequency"][grade]["label"],
            "total_deployments": total,
            "period_days": days,
        }

    @staticmethod
    def lead_time_for_changes(prs: list[dict], deployments: list[dict]) -> dict:
        if not prs:
            return {
                "value": 0,
                "unit": "hours",
                "grade": "Low",
                "benchmark": _DORA_BENCHMARKS["lead_time"]["Low"]["label"],
                "sample_size": 0,
            }

        deploy_times: dict[str, datetime] = {}
        for d in deployments:
            sha = d.get("sha", "")
            dt = _parse_dt(d.get("created_at"))
            if sha and dt:
                deploy_times[sha] = dt

        lead_times_hours: list[float] = []
        for pr in prs:
            created = _parse_dt(pr.get("created_at"))
            merged = _parse_dt(pr.get("merged_at"))

            deploy_time = deploy_times.get(pr.get("head_sha", ""))

            if deploy_time and created:
                lt = (deploy_time - created).total_seconds() / 3600
            elif merged and created:
                lt = (merged - created).total_seconds() / 3600
            else:
                continue

            if lt >= 0:
                lead_times_hours.append(lt)

        if not lead_times_hours:
            return {
                "value": 0,
                "unit": "hours",
                "grade": "Low",
                "benchmark": _DORA_BENCHMARKS["lead_time"]["Low"]["label"],
                "sample_size": 0,
            }

        median_hours = statistics.median(lead_times_hours)

        if median_hours <= 1:
            grade = "Elite"
        elif median_hours <= 24:
            grade = "High"
        elif median_hours <= 168:
            grade = "Medium"
        else:
            grade = "Low"

        return {
            "value": round(median_hours, 1),
            "unit": "hours",
            "grade": grade,
            "benchmark": _DORA_BENCHMARKS["lead_time"][grade]["label"],
            "sample_size": len(lead_times_hours),
        }

    @staticmethod
    def change_failure_rate(deployments: list[dict]) -> dict:
        if not deployments:
            return {
                "value": 0,
                "unit": "percent",
                "grade": "Low",
                "benchmark": _DORA_BENCHMARKS["change_failure_rate"]["Low"]["label"],
                "total_deployments": 0,
                "failed_deployments": 0,
            }

        failed = sum(1 for d in deployments if d.get("status") in ("failure", "error"))
        rate = (failed / len(deployments)) * 100

        if rate <= 5:
            grade = "Elite"
        elif rate <= 10:
            grade = "High"
        elif rate <= 15:
            grade = "Medium"
        else:
            grade = "Low"

        return {
            "value": round(rate, 1),
            "unit": "percent",
            "grade": grade,
            "benchmark": _DORA_BENCHMARKS["change_failure_rate"][grade]["label"],
            "total_deployments": len(deployments),
            "failed_deployments": failed,
        }

    @staticmethod
    def mean_time_to_recovery(deployments: list[dict]) -> dict:
        if not deployments:
            return {
                "value": 0,
                "unit": "hours",
                "grade": "Low",
                "benchmark": _DORA_BENCHMARKS["mttr"]["Low"]["label"],
                "incidents": 0,
            }

        sorted_deps = sorted(deployments, key=lambda d: d.get("created_at", ""))
        recovery_times: list[float] = []

        i = 0
        while i < len(sorted_deps):
            if sorted_deps[i].get("status") in ("failure", "error"):
                failure_time = _parse_dt(sorted_deps[i].get("created_at"))
                j = i + 1
                while j < len(sorted_deps):
                    if sorted_deps[j].get("status") == "success":
                        recovery_time = _parse_dt(sorted_deps[j].get("created_at"))
                        if failure_time and recovery_time:
                            hours = (recovery_time - failure_time).total_seconds() / 3600
                            if hours >= 0:
                                recovery_times.append(hours)
                        break
                    j += 1
                i = j + 1 if j < len(sorted_deps) else len(sorted_deps)
            else:
                i += 1

        if not recovery_times:
            return {
                "value": 0,
                "unit": "hours",
                "grade": "Elite",
                "benchmark": _DORA_BENCHMARKS["mttr"]["Elite"]["label"],
                "incidents": 0,
            }

        median_hours = statistics.median(recovery_times)

        if median_hours <= 1:
            grade = "Elite"
        elif median_hours <= 24:
            grade = "High"
        elif median_hours <= 168:
            grade = "Medium"
        else:
            grade = "Low"

        return {
            "value": round(median_hours, 1),
            "unit": "hours",
            "grade": grade,
            "benchmark": _DORA_BENCHMARKS["mttr"][grade]["label"],
            "incidents": len(recovery_times),
        }

    @staticmethod
    def ai_rework_rate(prs: list[dict]) -> dict:
        if not prs:
            return {
                "value": 0,
                "unit": "percent",
                "grade": "info",
                "benchmark": "experimental — tracks reverts, hotfixes, and regression fixes",
                "total_prs": 0,
                "rework_prs": 0,
            }

        rework_count = sum(1 for pr in prs if _REWORK_PATTERNS.search(pr.get("title", "")))
        rate = (rework_count / len(prs)) * 100

        return {
            "value": round(rate, 1),
            "unit": "percent",
            "grade": "info",
            "benchmark": "experimental — tracks reverts, hotfixes, and regression fixes",
            "total_prs": len(prs),
            "rework_prs": rework_count,
        }

    @staticmethod
    def overall_score(metrics: dict) -> dict:
        weighted = 0.0
        for key, weight in _GRADE_WEIGHTS.items():
            grade = metrics.get(key, {}).get("grade", "Low")
            weighted += _LEVEL_SCORES.get(grade, 25) * weight

        score = round(weighted)

        if score >= 90:
            letter, level = "A", "Elite"
        elif score >= 75:
            letter, level = "B", "High"
        elif score >= 50:
            letter, level = "C", "Medium"
        else:
            letter, level = "D", "Low"

        return {"score": score, "grade": letter, "level": level}


# ---------------------------------------------------------------------------
# Recommendations engine
# ---------------------------------------------------------------------------


_RECOMMENDATIONS: dict[str, dict[str, list[str]]] = {
    "deployment_frequency": {
        "Low": [
            "Enable CI/CD auto-deploy on merge to reduce manual deployment friction",
            "Break monolithic releases into smaller, incremental deployments",
            "Adopt feature flags to decouple deploy from release",
        ],
        "Medium": [
            "Automate deployment gates to reduce manual approval bottlenecks",
            "Consider implementing trunk-based development to accelerate flow",
        ],
    },
    "lead_time": {
        "Low": [
            "Reduce PR review cycle time — set SLAs for review turnaround",
            "Break large PRs into smaller, reviewable chunks",
            "Automate build and test to remove manual verification steps",
        ],
        "Medium": [
            "Add parallel test execution to reduce CI pipeline duration",
            "Implement auto-merge for low-risk, fully-tested changes",
        ],
    },
    "change_failure_rate": {
        "Low": [
            "Add pre-deploy smoke tests and canary deployments",
            "Improve test coverage for critical paths",
            "Implement progressive rollout (1% → 10% → 50% → 100%)",
        ],
        "Medium": [
            "Add integration tests that run against staging before prod deploy",
            "Review change failure patterns — are failures concentrated in specific areas?",
        ],
    },
    "mttr": {
        "Low": [
            "Implement automated rollback on deployment failure",
            "Add health check probes with aggressive timeouts",
            "Create runbooks for common failure scenarios",
        ],
        "Medium": [
            "Add real-time alerting on deployment failures",
            "Practice incident response drills quarterly",
        ],
    },
}


def _build_summary(repo: str, overall: dict, areas: list[dict]) -> str:
    base = f"{repo} is at {overall['level']} level (score: {overall['score']}/100). "
    if areas:
        metric = areas[0]["metric"].replace("_", " ")
        grade = areas[0]["current_grade"]
        return base + f"Focus on {metric} first — currently graded {grade}."
    return base + "All metrics are at Elite level — maintain current practices."


def _generate_recommendations(metrics: dict) -> list[str]:
    recs = []
    for metric_key in ("deployment_frequency", "lead_time", "change_failure_rate", "mttr"):
        grade = metrics.get(metric_key, {}).get("grade", "")
        if grade in _RECOMMENDATIONS.get(metric_key, {}):
            recs.extend(_RECOMMENDATIONS[metric_key][grade])
    return recs


# ---------------------------------------------------------------------------
# Agent tool functions
# ---------------------------------------------------------------------------


async def get_dora_metrics(repo: str, days: int = 30) -> dict:
    """Get DORA metrics for a single repository.

    Calculates all 4 DORA metrics (deployment frequency, lead time for changes,
    change failure rate, mean time to recovery) plus the AI rework rate. Each
    metric is graded against industry benchmarks (Elite/High/Medium/Low).

    Args:
        repo: Repository name (e.g., 'sample-nextjs-app' or 'org/repo').
        days: Analysis period in days (default: 30).

    Returns:
        Complete DORA report with metrics, grades, overall score, and recommendations.
    """
    try:
        reg = _get_registry()
        github = reg.github
        collector = MetricsCollector(github._client, github._org)
        calculator = MetricsCalculator()

        deployments = await collector.collect_deployments(repo, days)
        prs = await collector.collect_pull_requests(repo, days)

        metrics = {
            "deployment_frequency": calculator.deployment_frequency(deployments, days),
            "lead_time": calculator.lead_time_for_changes(prs, deployments),
            "change_failure_rate": calculator.change_failure_rate(deployments),
            "mttr": calculator.mean_time_to_recovery(deployments),
            "ai_rework_rate": calculator.ai_rework_rate(prs),
        }

        overall = calculator.overall_score(metrics)
        recommendations = _generate_recommendations(metrics)

        logger.info(
            "dora_metrics_calculated",
            repo=repo,
            days=days,
            score=overall["score"],
            level=overall["level"],
        )

        return {
            "repo": repo,
            "period_days": days,
            "generated_at": datetime.now(UTC).isoformat(),
            "metrics": metrics,
            "overall": overall,
            "recommendations": recommendations,
        }
    except httpx.HTTPStatusError as exc:
        status = exc.response.status_code
        if status == 404:
            return {"error": "repo_not_found", "details": f"Repository '{repo}' not found."}
        if status in (401, 403):
            return {"error": "auth_error", "details": "GitHub API authentication failed."}
        return {"error": "api_error", "details": f"GitHub API error: HTTP {status}"}
    except RuntimeError as exc:
        return {"error": "not_initialized", "details": str(exc)}


async def get_team_metrics(repos: str, days: int = 30) -> dict:
    """Get aggregated DORA metrics for a team (multiple repositories).

    Calculates DORA metrics for each repo individually, then aggregates into
    team-level rollups using weighted averages.

    Args:
        repos: Comma-separated repository names (e.g., 'repo-a,repo-b,repo-c').
        days: Analysis period in days (default: 30).

    Returns:
        Per-repo metrics plus aggregated team-level scores.
    """
    repo_list = [r.strip() for r in repos.split(",") if r.strip()]
    if not repo_list:
        return {"error": "invalid_input", "details": "No repositories provided."}

    per_repo: list[dict] = []
    errors: list[dict] = []

    for repo in repo_list:
        result = await get_dora_metrics(repo, days)
        if "error" in result:
            errors.append({"repo": repo, **result})
        else:
            per_repo.append(result)

    if not per_repo:
        return {
            "error": "no_data",
            "details": "Could not calculate metrics for any repository.",
            "errors": errors,
        }

    agg_metrics: dict[str, dict] = {}
    for metric_key in ("deployment_frequency", "lead_time", "change_failure_rate", "mttr"):
        values = [r["metrics"][metric_key]["value"] for r in per_repo]
        grades = [r["metrics"][metric_key]["grade"] for r in per_repo]
        avg = round(statistics.mean(values), 2)
        grade_scores = [_LEVEL_SCORES.get(g, 25) for g in grades]
        avg_grade_score = statistics.mean(grade_scores)

        if avg_grade_score >= 87.5:
            agg_grade = "Elite"
        elif avg_grade_score >= 62.5:
            agg_grade = "High"
        elif avg_grade_score >= 37.5:
            agg_grade = "Medium"
        else:
            agg_grade = "Low"

        agg_metrics[metric_key] = {
            "value": avg,
            "unit": per_repo[0]["metrics"][metric_key]["unit"],
            "grade": agg_grade,
            "benchmark": _DORA_BENCHMARKS.get(metric_key, {}).get(agg_grade, {}).get("label", ""),
        }

    rework_values = [r["metrics"]["ai_rework_rate"]["value"] for r in per_repo]
    agg_metrics["ai_rework_rate"] = {
        "value": round(statistics.mean(rework_values), 1),
        "unit": "percent",
        "grade": "info",
        "benchmark": "experimental — team-level aggregate",
    }

    calculator = MetricsCalculator()
    overall = calculator.overall_score(agg_metrics)

    return {
        "team_repos": [r["repo"] for r in per_repo],
        "period_days": days,
        "generated_at": datetime.now(UTC).isoformat(),
        "aggregated_metrics": agg_metrics,
        "overall": overall,
        "per_repo": [
            {
                "repo": r["repo"],
                "score": r["overall"]["score"],
                "level": r["overall"]["level"],
            }
            for r in per_repo
        ],
        "recommendations": _generate_recommendations(agg_metrics),
        "errors": errors if errors else None,
    }


async def compare_repos(repos: str, days: int = 30) -> dict:
    """Compare DORA metrics side-by-side across multiple repositories.

    Shows each metric for each repo in a comparison format, highlighting
    the strongest and weakest performers.

    Args:
        repos: Comma-separated repository names (e.g., 'repo-a,repo-b').
        days: Analysis period in days (default: 30).

    Returns:
        Side-by-side comparison with per-metric rankings.
    """
    repo_list = [r.strip() for r in repos.split(",") if r.strip()]
    if len(repo_list) < 2:
        return {"error": "invalid_input", "details": "At least 2 repositories required."}

    results: list[dict] = []
    for repo in repo_list:
        result = await get_dora_metrics(repo, days)
        if "error" not in result:
            results.append(result)

    if len(results) < 2:
        return {
            "error": "insufficient_data",
            "details": "Need metrics from at least 2 repos to compare.",
        }

    comparison: dict[str, list[dict]] = {}
    for metric_key in ("deployment_frequency", "lead_time", "change_failure_rate", "mttr"):
        entries = []
        for r in results:
            m = r["metrics"][metric_key]
            entries.append({
                "repo": r["repo"],
                "value": m["value"],
                "grade": m["grade"],
            })

        reverse = metric_key == "deployment_frequency"
        entries.sort(key=lambda x: x["value"], reverse=reverse)

        for rank, entry in enumerate(entries, 1):
            entry["rank"] = rank

        comparison[metric_key] = entries

    return {
        "period_days": days,
        "generated_at": datetime.now(UTC).isoformat(),
        "comparison": comparison,
        "overall_ranking": sorted(
            [{"repo": r["repo"], **r["overall"]} for r in results],
            key=lambda x: x["score"],
            reverse=True,
        ),
    }


async def get_metric_trends(repo: str, periods: int = 3, period_days: int = 30) -> dict:
    """Get DORA metric trends over multiple time periods.

    Shows how metrics have changed over N consecutive periods to identify
    improvement or degradation patterns.

    Args:
        repo: Repository name.
        periods: Number of periods to compare (default: 3).
        period_days: Length of each period in days (default: 30).

    Returns:
        Historical metrics with period-over-period deltas.
    """
    if periods < 2:
        return {"error": "invalid_input", "details": "At least 2 periods required for trends."}
    if periods > 6:
        periods = 6

    period_results: list[dict] = []
    for i in range(periods):
        result = await get_dora_metrics(repo, period_days)
        if "error" in result:
            return result

        label_end = datetime.now(UTC) - timedelta(days=period_days * i)
        label_start = label_end - timedelta(days=period_days)

        period_results.append({
            "period": f"{label_start.strftime('%Y-%m-%d')} to {label_end.strftime('%Y-%m-%d')}",
            "period_index": i,
            "metrics": result["metrics"],
            "overall": result["overall"],
        })

    trends: dict[str, dict] = {}
    for metric_key in ("deployment_frequency", "lead_time", "change_failure_rate", "mttr"):
        current = period_results[0]["metrics"][metric_key]["value"]
        previous = period_results[-1]["metrics"][metric_key]["value"]

        if previous > 0:
            delta_pct = round(((current - previous) / previous) * 100, 1)
        elif current > 0:
            delta_pct = 100.0
        else:
            delta_pct = 0.0

        improving = (
            (delta_pct > 0 and metric_key == "deployment_frequency")
            or (delta_pct < 0 and metric_key in ("lead_time", "change_failure_rate", "mttr"))
        )

        trends[metric_key] = {
            "current": current,
            "previous": previous,
            "delta_percent": delta_pct,
            "direction": (
                "improving" if improving else ("stable" if delta_pct == 0 else "degrading")
            ),
        }

    return {
        "repo": repo,
        "period_days": period_days,
        "total_periods": periods,
        "generated_at": datetime.now(UTC).isoformat(),
        "trends": trends,
        "periods": period_results,
    }


async def get_dora_recommendations(repo: str) -> dict:
    """Get improvement recommendations based on a repository's weakest DORA metrics.

    Analyzes the current DORA metrics and provides actionable, prioritized
    recommendations focusing on the lowest-performing areas.

    Args:
        repo: Repository name.

    Returns:
        Prioritized recommendations grouped by metric area.
    """
    result = await get_dora_metrics(repo)
    if "error" in result:
        return result

    metrics = result["metrics"]
    overall = result["overall"]

    areas: list[dict] = []
    for metric_key in ("deployment_frequency", "lead_time", "change_failure_rate", "mttr"):
        grade = metrics[metric_key]["grade"]
        score = _LEVEL_SCORES.get(grade, 25)
        recs = _RECOMMENDATIONS.get(metric_key, {}).get(grade, [])
        if recs:
            areas.append({
                "metric": metric_key,
                "current_grade": grade,
                "current_value": metrics[metric_key]["value"],
                "unit": metrics[metric_key]["unit"],
                "priority": "high" if score <= 25 else "medium",
                "recommendations": recs,
            })

    areas.sort(key=lambda x: _LEVEL_SCORES.get(x["current_grade"], 25))

    return {
        "repo": repo,
        "overall_score": overall["score"],
        "overall_level": overall["level"],
        "improvement_areas": areas,
        "summary": _build_summary(repo, overall, areas),
    }
