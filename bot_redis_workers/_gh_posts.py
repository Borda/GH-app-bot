from datetime import datetime

from gidgethub.aiohttp import GitHubAPI

from _bots_commons.configs import ConfigFile, ConfigWorkflow, GitHubRunConclusion, GitHubRunStatus
from _bots_commons.utils import gh_patch_with_retry, gh_post_with_retry


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
    patch_data = {
        "status": run_status.value,
        "started_at": started_at,
        "output": {"summary": summary},
        "details_url": url_job,
    }
    if title:
        patch_data["output"].update({"title": title})
    if text:
        patch_data["output"].update({"text": text})
    if run_conclusion:
        patch_data.update({"conclusion": run_conclusion.value})
    await gh_patch_with_retry(gh=gh, url=gh_url, data=patch_data)
