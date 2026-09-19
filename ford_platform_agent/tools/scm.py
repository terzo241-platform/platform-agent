"""Source control tools — repository and PR operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ford_platform_agent.providers.registry import ProviderRegistry

_registry: ProviderRegistry | None = None


def set_registry(registry: ProviderRegistry) -> None:
    global _registry
    _registry = registry


def _get_registry() -> ProviderRegistry:
    if _registry is None:
        raise RuntimeError("ProviderRegistry not initialized.")
    return _registry


async def list_repositories(org: str = "") -> dict:
    """List all repositories in the organization.

    Args:
        org: Organization name. Leave empty to use the default org from config.

    Returns:
        List of repositories with name, language, description, and URL.
    """
    reg = _get_registry()
    scm = reg.get_scm()
    repos = await scm.list_repos(org or None)
    return {
        "provider": scm.name,
        "total": len(repos),
        "repositories": [
            {
                "name": r.name,
                "full_name": r.full_name,
                "language": r.language,
                "description": r.description,
                "default_branch": r.default_branch,
                "url": r.url,
            }
            for r in repos
        ],
    }


async def get_repository_info(repo: str) -> dict:
    """Get detailed information about a specific repository.

    Args:
        repo: Repository name (e.g., 'sample-nextjs-app').

    Returns:
        Repository details including language, description, and default branch.
    """
    reg = _get_registry()
    scm = reg.get_scm()
    r = await scm.get_repo(repo)
    return {
        "name": r.name,
        "full_name": r.full_name,
        "language": r.language,
        "description": r.description,
        "default_branch": r.default_branch,
        "url": r.url,
        "ci_provider": r.ci_provider,
    }


async def list_pull_requests(repo: str, state: str = "open") -> dict:
    """List pull requests for a repository.

    Args:
        repo: Repository name.
        state: Filter by state — 'open', 'closed', or 'all'.

    Returns:
        List of pull requests with title, author, branch, and URL.
    """
    reg = _get_registry()
    scm = reg.get_scm()
    prs = await scm.list_pull_requests(repo, state)
    return {
        "total": len(prs),
        "pull_requests": [
            {
                "number": pr.number,
                "title": pr.title,
                "state": pr.state,
                "author": pr.author,
                "branch": pr.branch,
                "url": pr.url,
            }
            for pr in prs
        ],
    }


async def create_pull_request(
    repo: str, title: str, body: str, head_branch: str, base_branch: str = "main"
) -> dict:
    """Create a new pull request.

    This is a WRITE operation that creates a visible PR in the repository.

    Args:
        repo: Repository name.
        title: PR title (keep under 70 characters).
        body: PR description in markdown.
        head_branch: Source branch with changes.
        base_branch: Target branch to merge into (default: 'main').

    Returns:
        Created PR details with number and URL.
    """
    reg = _get_registry()
    scm = reg.get_scm()
    pr = await scm.create_pull_request(repo, title, body, head_branch, base_branch)
    return {
        "number": pr.number,
        "title": pr.title,
        "url": pr.url,
        "message": f"PR #{pr.number} created: {pr.title}",
    }
