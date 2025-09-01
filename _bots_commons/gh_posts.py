import asyncio
import time
from datetime import datetime
from typing import Any

import jwt
from aiohttp import ClientSession, client_exceptions
from gidgethub.aiohttp import GitHubAPI
from gidgethub.apps import get_installation_access_token

from _bots_commons.configs import ConfigFile, ConfigWorkflow, GitHubRunConclusion, GitHubRunStatus
from _bots_commons.utils import load_validate_required_env_vars


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
    github_app_id, app_private_key, _ = load_validate_required_env_vars()
    jwt_token = create_jwt_token(github_app_id=github_app_id, app_private_key=app_private_key)
    # Exchange JWT for installation token
    app_gh = GitHubAPI(session, "bot_redis_workers", oauth_token=jwt_token)
    installation_id = payload["installation"]["id"]
    token_resp = await get_installation_access_token(
        app_gh, installation_id=installation_id, app_id=str(github_app_id), private_key=app_private_key
    )
    return token_resp["token"]


def create_jwt_token(github_app_id: int, app_private_key: str) -> str:
    """Create a JWT token for authenticating with the GitHub API.

    Args:
        github_app_id: GitHub App ID.
        app_private_key: PEM-encoded private key.

    Returns:
        Encoded JWT string.
    """
    return jwt.encode(
        {"iat": int(time.time()) - 60, "exp": int(time.time()) + (10 * 60), "iss": github_app_id},
        app_private_key,
        algorithm="RS256",
    )


async def gh_post_with_retry(gh: Any, url: str, data: dict, retries: int = 3, backoff: float = 1.0) -> Any:
    """POST to GitHub API with simple retry on ServerDisconnectedError.

    Args:
        gh: GitHub API client (e.g., gidgethub.aiohttp.GitHubAPI).
        url: API endpoint path.
        data: JSON-serializable payload.
        retries: Number of retries.
        backoff: Base backoff seconds (linear backoff: backoff * attempt).

    Returns:
        The response from gh.post or None if all retries fail.

    Raises:
        client_exceptions.ServerDisconnectedError: Propagates on final attempt.
    """
    for it in range(1, retries + 1):
        try:
            return await gh.post(url, data=data)
        except client_exceptions.ServerDisconnectedError:
            if it == retries:
                raise
            await asyncio.sleep(backoff * it)
    return None


async def gh_patch_with_retry(gh: Any, url: str, data: dict, retries: int = 3, backoff: float = 1.0) -> Any:
    """PATCH to GitHub API with simple retry on ServerDisconnectedError.

    Args:
        gh: GitHub API client (e.g., gidgethub.aiohttp.GitHubAPI).
        url: API endpoint path.
        data: JSON-serializable payload.
        retries: Number of retries.
        backoff: Base backoff seconds (linear backoff: backoff * attempt).

    Returns:
        The response from gh.patch or None if all retries fail.

    Raises:
        client_exceptions.ServerDisconnectedError: Propagates on final attempt.
    """
    for it in range(1, retries + 1):
        try:
            return await gh.patch(url, data=data)
        except client_exceptions.ServerDisconnectedError:
            if it == retries:
                raise
            await asyncio.sleep(backoff * it)
    return None
