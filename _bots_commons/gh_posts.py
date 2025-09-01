from datetime import datetime
from typing import Any

from aiohttp import ClientSession
from gidgethub.aiohttp import GitHubAPI
from gidgethub.apps import get_installation_access_token

from _bots_commons.configs import ConfigFile, ConfigWorkflow, GitHubRunConclusion, GitHubRunStatus
from _bots_commons.utils import (
    create_jwt_token,
    gh_patch_with_retry,
    gh_post_with_retry,
    load_validate_required_env_vars,
)


async def post_gh_run_status_missing_configs(gh: GitHubAPI, gh_url: str, head_sha: str, text: str) -> None:
    """Post a GitHub run status with missing configs."""
    await gh_post_with_retry(
        gh=gh,
        url=gh_url,
        data={
            "name": "Lit bot",
            "head_sha": head_sha,
            "status": GitHubRunStatus.COMPLETED.value,
            "conclusion": GitHubRunConclusion.SKIPPED.value,
            "started_at": datetime.utcnow().isoformat() + "Z",
            "output": {
                "title": "No Configs Found",
                "summary": "No valid configuration files found in `.lightning/workflows` folder.",
                "text": text,
            },
        },
    )


async def post_gh_run_status_not_triggered(
    gh: GitHubAPI,
    gh_url: str,
    head_sha: str,
    event_type: str,
    branch_ref: str,
    cfg_file: ConfigFile,
    config: ConfigWorkflow,
) -> None:
    """Post a GitHub run status with missing configs."""
    await gh_post_with_retry(
        gh=gh,
        url=gh_url,
        data={
            "name": f"{cfg_file.name} / {config.name} [{event_type}]",
            "head_sha": head_sha,
            "status": GitHubRunStatus.COMPLETED.value,
            "conclusion": GitHubRunConclusion.SKIPPED.value,
            "started_at": datetime.utcnow().isoformat() + "Z",
            "output": {
                "title": "Skipped",
                "summary": f"Configuration `{cfg_file.name}` is not triggered"
                f" by the event `{event_type}` on branch `{branch_ref}` (with `{config.trigger}`).",
            },
        },
    )


async def post_gh_run_status_create_check(
    gh: GitHubAPI, gh_url: str, head_sha: str, run_name: str, link_lit_jobs: str
) -> str:
    """Create a GitHub run status."""
    check = await gh_post_with_retry(
        gh=gh,
        url=gh_url,
        data={
            "name": run_name,
            "head_sha": head_sha,
            "status": GitHubRunStatus.QUEUED.value,
            "details_url": link_lit_jobs,
        },
    )
    return check["id"]


async def post_gh_run_status_update_check(
    gh: GitHubAPI,
    gh_url: str,
    title: str,
    run_status: GitHubRunStatus,
    run_conclusion: GitHubRunConclusion | None = None,
    started_at: str = "",
    url_job: str = "",
    summary: str = "",
    text: str = "",
) -> None:
    """Update a GitHub run status."""
    if not started_at:
        started_at = datetime.utcnow().isoformat() + "Z"
    patch_data = {"status": run_status.value, "started_at": started_at, "output": {"summary": summary}}
    if title:
        patch_data["output"]["title"] = title
    if text:
        patch_data["output"]["text"] = text
    if url_job:
        patch_data["details_url"] = url_job
    if run_conclusion:
        patch_data["conclusion"] = run_conclusion.value
    await gh_patch_with_retry(gh=gh, url=gh_url, data=patch_data)


async def _get_gh_app_token(session: ClientSession, payload: dict[str, Any]) -> str:
    """Get an installation access token for the GitHub App.

    Args:
        session: aiohttp client session to use for GitHub API calls.
        payload: Parsed webhook payload containing installation info.

    Returns:
        Installation access token string.
    """
    github_app_id, app_private_key, webhook_secret = load_validate_required_env_vars()
    jwt_token = create_jwt_token(github_app_id=github_app_id, app_private_key=app_private_key)
    # Exchange JWT for installation token
    app_gh = GitHubAPI(session, "bot_redis_workers", oauth_token=jwt_token)
    installation_id = payload["installation"]["id"]
    token_resp = await get_installation_access_token(
        app_gh, installation_id=installation_id, app_id=str(github_app_id), private_key=app_private_key
    )
    return token_resp["token"]
