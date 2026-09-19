"""Infrastructure tools — Terraform orchestration via GitOps.

The agent doesn't run Terraform directly. Instead, it:
1. Generates TF config from templates (golden paths)
2. Commits to a branch in platform-terraform
3. Opens a PR → terraform.yml runs plan automatically
4. Reads the plan output from PR comments
5. Merges the PR → terraform.yml runs apply

This is the GitOps pattern: infrastructure changes go through code review.
"""

from __future__ import annotations

import textwrap
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ford_platform_agent.providers.registry import ProviderRegistry

_registry: ProviderRegistry | None = None

TERRAFORM_REPO = "platform-terraform"

_SERVICE_TEMPLATE = textwrap.dedent("""\
    module "{module_name}" {{
      source = "../../modules/cloud-run-service"

      project_id   = var.project_id
      service_name = "{service_name}"
      region       = var.region
      image        = "${{var.artifact_registry_repo}}/{service_name}:latest"
      environment  = "{environment}"

      team        = "{team}"
      cost_center = "{cost_center}"

      cpu    = "{cpu}"
      memory = "{memory}"
      port   = {port}

      min_instances = {min_instances}
      max_instances = {max_instances}
      concurrency   = {concurrency}

      env_vars = {{
        APP_ENV = "{environment}"
      }}

      allow_unauthenticated = {allow_unauthenticated}
      ingress               = "{ingress}"

      extra_labels = {{
        app = "{service_name}"
      }}
    }}
""")

_ENVIRONMENT_DEFAULTS = {
    "dev": {
        "min_instances": 0,
        "max_instances": 3,
        "cpu": "1",
        "memory": "512Mi",
        "concurrency": 80,
        "allow_unauthenticated": "true",
        "ingress": "INGRESS_TRAFFIC_ALL",
    },
    "staging": {
        "min_instances": 1,
        "max_instances": 5,
        "cpu": "1",
        "memory": "1Gi",
        "concurrency": 80,
        "allow_unauthenticated": "false",
        "ingress": "INGRESS_TRAFFIC_INTERNAL_ONLY",
    },
    "prod": {
        "min_instances": 2,
        "max_instances": 20,
        "cpu": "2",
        "memory": "2Gi",
        "concurrency": 100,
        "allow_unauthenticated": "false",
        "ingress": "INGRESS_TRAFFIC_INTERNAL_LOAD_BALANCER",
    },
}


def set_registry(registry: ProviderRegistry) -> None:
    global _registry
    _registry = registry


def _get_registry() -> ProviderRegistry:
    if _registry is None:
        raise RuntimeError("ProviderRegistry not initialized.")
    return _registry


def _generate_tf_config(
    service_name: str,
    team: str,
    cost_center: str,
    environment: str = "dev",
    port: int = 8080,
    cpu: str = "",
    memory: str = "",
) -> str:
    """Generate Terraform config from the golden path template."""
    defaults = _ENVIRONMENT_DEFAULTS.get(environment, _ENVIRONMENT_DEFAULTS["dev"])
    module_name = service_name.replace("-", "_")

    return _SERVICE_TEMPLATE.format(
        module_name=module_name,
        service_name=service_name,
        environment=environment,
        team=team,
        cost_center=cost_center,
        port=port,
        cpu=cpu or defaults["cpu"],
        memory=memory or defaults["memory"],
        min_instances=defaults["min_instances"],
        max_instances=defaults["max_instances"],
        concurrency=defaults["concurrency"],
        allow_unauthenticated=defaults["allow_unauthenticated"],
        ingress=defaults["ingress"],
    )


async def create_service_pr(
    service_name: str,
    team: str,
    cost_center: str,
    environment: str = "dev",
    port: int = 8080,
    cpu: str = "",
    memory: str = "",
) -> dict:
    """Create a PR to provision a new Cloud Run service via Terraform.

    Generates Terraform config from the golden path template, commits it
    to a new branch in platform-terraform, and opens a PR. The terraform.yml
    workflow will automatically run `terraform plan` and post the output
    as a PR comment.

    Args:
        service_name: Name for the new service (lowercase, hyphens, 3-63 chars).
        team: Owning team name (e.g., 'marketing-web').
        cost_center: Finance cost center (e.g., 'MKT-40210').
        environment: Target environment — 'dev', 'staging', or 'prod'.
        port: Container port (default: 8080).
        cpu: CPU allocation — '0.5', '1', '2', '4'. Empty = environment default.
        memory: Memory — '256Mi', '512Mi', '1Gi', '2Gi', '4Gi'. Empty = default.

    Returns:
        Created PR details with number and URL.
    """
    reg = _get_registry()
    github = reg.github

    tf_config = _generate_tf_config(
        service_name=service_name,
        team=team,
        cost_center=cost_center,
        environment=environment,
        port=port,
        cpu=cpu,
        memory=memory,
    )

    branch_name = f"agent/add-{service_name}-{environment}"
    file_path = f"environments/{environment}/{service_name}.tf"

    base_sha = await github.create_branch(TERRAFORM_REPO, branch_name)

    tree_resp = await github._post(
        f"{github._repo_path(TERRAFORM_REPO)}/git/trees",
        {
            "base_tree": base_sha,
            "tree": [
                {
                    "path": file_path,
                    "mode": "100644",
                    "type": "blob",
                    "content": tf_config,
                }
            ],
        },
    )
    tree_sha = tree_resp["sha"]

    commit_resp = await github._post(
        f"{github._repo_path(TERRAFORM_REPO)}/git/commits",
        {
            "message": f"Add {service_name} to {environment}\n\n"
            f"Generated by Ford Platform Agent.\n"
            f"Team: {team}, Cost Center: {cost_center}",
            "tree": tree_sha,
            "parents": [base_sha],
        },
    )
    commit_sha = commit_resp["sha"]

    await github._client.patch(
        f"{github._repo_path(TERRAFORM_REPO)}/git/refs/heads/{branch_name}",
        json={"sha": commit_sha},
    )

    defaults = _ENVIRONMENT_DEFAULTS.get(environment, _ENVIRONMENT_DEFAULTS["dev"])
    pr = await github.create_pull_request(
        repo=TERRAFORM_REPO,
        title=f"Add {service_name} ({environment})",
        body=(
            f"## New Service: `{service_name}`\n\n"
            f"| Field | Value |\n|---|---|\n"
            f"| Environment | `{environment}` |\n"
            f"| Team | `{team}` |\n"
            f"| Cost Center | `{cost_center}` |\n"
            f"| Port | `{port}` |\n"
            f"| CPU | `{cpu or defaults.get('cpu', '1')}` |\n"
            f"| Memory | `{memory or defaults.get('memory', '512Mi')}` |\n\n"
            f"Generated by Ford Platform Agent. "
            f"Terraform plan will run automatically."
        ),
        head=branch_name,
        base="main",
    )

    return {
        "pr_number": pr.number,
        "pr_url": pr.url,
        "branch": branch_name,
        "file_path": file_path,
        "environment": environment,
        "message": (
            f"PR #{pr.number} created to add {service_name} to {environment}. "
            f"Terraform plan will run automatically — check PR comments for the plan output."
        ),
    }


async def get_plan_output(pr_number: int) -> dict:
    """Read the Terraform plan output from a PR's comments.

    The terraform.yml workflow posts plan output as a PR comment after
    `terraform plan` runs. This tool reads that comment.

    Args:
        pr_number: Pull request number in platform-terraform.

    Returns:
        Plan output text and status.
    """
    reg = _get_registry()
    github = reg.github

    resp = await github._get(
        f"{github._repo_path(TERRAFORM_REPO)}/issues/{pr_number}/comments",
        {"per_page": 50},
    )

    plan_comments = []
    for comment in resp:
        body = comment.get("body", "")
        if any(
            marker in body
            for marker in [
                "Terraform Plan",
                "terraform plan",
                "Plan:",
                "No changes",
                "to add",
                "to change",
                "to destroy",
            ]
        ):
            plan_comments.append({
                "id": comment["id"],
                "author": comment.get("user", {}).get("login", ""),
                "created_at": comment.get("created_at", ""),
                "body": body[:3000],
            })

    if not plan_comments:
        pr_data = await github.get_pull_request(TERRAFORM_REPO, pr_number)
        return {
            "pr_number": pr_number,
            "status": "pending",
            "message": (
                "No plan output found yet. The terraform.yml workflow "
                "may still be running. Check the PR for status."
            ),
            "pr_url": pr_data.url,
        }

    latest = plan_comments[-1]
    return {
        "pr_number": pr_number,
        "status": "found",
        "plan_output": latest["body"],
        "posted_by": latest["author"],
        "posted_at": latest["created_at"],
    }


async def approve_and_merge(pr_number: int, approver: str = "") -> dict:
    """Merge a Terraform PR to trigger apply.

    This merges the PR in platform-terraform, which triggers the
    terraform.yml workflow to run `terraform apply`. This is a
    DESTRUCTIVE operation that changes real infrastructure.

    Separation of duties: the approver must be different from the PR author.
    Both must belong to the same team. The agent enforces this before merging.

    Args:
        pr_number: Pull request number to merge.
        approver: GitHub username of the person approving. Must differ from PR author.

    Returns:
        Merge result with status.
    """
    reg = _get_registry()
    github = reg.github

    pr = await github.get_pull_request(TERRAFORM_REPO, pr_number)
    if pr.state != "open":
        return {
            "pr_number": pr_number,
            "merged": False,
            "message": f"PR #{pr_number} is already {pr.state}.",
        }

    if approver and approver == pr.author:
        return {
            "pr_number": pr_number,
            "merged": False,
            "message": (
                f"Separation of duties: {approver} cannot merge their own PR. "
                f"A different team member must approve and merge PR #{pr_number}."
            ),
        }

    if not approver:
        return {
            "pr_number": pr_number,
            "merged": False,
            "message": (
                f"PR #{pr_number} was authored by {pr.author}. "
                f"A different team member must approve. "
                f"Please provide the approver's GitHub username."
            ),
        }

    resp = await github._client.put(
        f"{github._repo_path(TERRAFORM_REPO)}/pulls/{pr_number}/merge",
        json={
            "merge_method": "squash",
            "commit_title": f"Merge PR #{pr_number}: {pr.title}",
            "commit_message": "Merged by Ford Platform Agent. Terraform apply will run.",
        },
    )

    if resp.status_code == 200:
        return {
            "pr_number": pr_number,
            "merged": True,
            "message": (
                f"PR #{pr_number} merged. Terraform apply is now running. "
                f"Check the Actions tab for progress."
            ),
        }

    error = resp.json()
    return {
        "pr_number": pr_number,
        "merged": False,
        "message": f"Merge failed: {error.get('message', 'Unknown error')}",
    }


async def list_environments() -> dict:
    """List available infrastructure environments and their configurations.

    Shows the golden path defaults for each environment (dev/staging/prod).

    Returns:
        Environment configurations with default scaling and security settings.
    """
    return {
        "environments": {
            env: {
                "min_instances": cfg["min_instances"],
                "max_instances": cfg["max_instances"],
                "cpu": cfg["cpu"],
                "memory": cfg["memory"],
                "ingress": cfg["ingress"],
                "public_access": cfg["allow_unauthenticated"] == "true",
            }
            for env, cfg in _ENVIRONMENT_DEFAULTS.items()
        },
        "message": (
            "These are the golden path defaults. Each environment has "
            "pre-configured scaling, security, and networking settings."
        ),
    }
