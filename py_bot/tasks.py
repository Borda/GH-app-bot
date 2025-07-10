import asyncio
import io
import os
import shutil
import zipfile
from pathlib import Path

import aiohttp
import yaml
from lightning_sdk import Job, Machine, Status

LOCAL_ROOT_DIR = Path(__file__).parent
LOCAL_TEMP_DIR = LOCAL_ROOT_DIR / ".temp"


async def run_sleeping_task(event):
    # Replace it with real logic; here we just succeed
    await asyncio.sleep(60)
    return True


async def _download_repo_and_extract(owner, repo, ref, token) -> str:
    """Download a GitHub repository at a specific ref (branch, tag, commit) and extract it to a temp directory."""
    # 1) Fetch zipball archive
    url = f"https://api.github.com/repos/{owner}/{repo}/zipball/{ref}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    async with aiohttp.ClientSession() as session, session.get(url, headers=headers) as resp:
        resp.raise_for_status()
        archive_data = await resp.read()
    print(f"Pull repo from {url}")

    # 2) Extract zip into a temp directory
    tempdir = LOCAL_TEMP_DIR.resolve()
    tempdir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(archive_data)) as zf:
        zf.extractall(tempdir)

    # 3) Locate the extracted root folder
    children = os.listdir(tempdir)
    if not children:
        return ""
    return os.path.join(tempdir, children[0])


async def run_repo_job(owner, repo, ref, token):
    """Download the full repo at `ref` into a tempdir, look for config file and execute the job."""
    repo_root = await _download_repo_and_extract(owner, repo, ref, token)
    if not repo_root:
        return False, f"Failed to download or extract repo {owner}/{repo} at {ref}"

    # Try to load the config file
    cfg_path = os.path.join(repo_root, ".lightning/actions.yaml")
    if not os.path.exists(cfg_path):
        return False, f"No config found at {cfg_path!r}"

    try:  # todo: add specific exception and yaml validation
        cfg_text = Path(cfg_path).read_text()
        config = yaml.safe_load(cfg_text)
        # mandatory
        config_run = config["run"]
        # optional
        docker_run_image = config.get("image", "ubuntu:22.04")
        config_env = config.get("env", {})
    except Exception as e:
        return False, f"Error parsing config: {e!s}"

    cmd_run = os.linesep.join(["ls -lah", config_run])
    # print(f"CMD: {cmd_run}")
    docker_run_script = ".lightning-actions.sh"
    cmd_path = os.path.join(repo_root, docker_run_script)
    assert not os.path.isfile(cmd_path), "the expected actions.sh file already exists"  # todo: add unique hash
    # dump the cmd to .lightning/actions.sh
    with open(cmd_path, "w", encoding="utf_8") as fp:
        fp.write(cmd_run + "\n")
    docker_run_env = " ".join([f'-e {k}="{v}"' for k, v in config_env.items()])
    job_cmd = (
        "docker run --rm "
        f"-v {repo_root}:/workspace "
        "-w /workspace "
        f"{docker_run_env} "
        f"{docker_run_image} "
        f"bash -lc 'ls -lah && cat {docker_run_script} && bash {docker_run_script}'"
    )
    print(f"job >> {job_cmd}")
    job = Job.run(
        name=f"ci-run_{owner}-{repo}-{ref}",
        command=job_cmd,
        machine=Machine.CPU,
        # interruptible=True,
    )
    job.wait()
    shutil.rmtree(os.path.dirname(repo_root), ignore_errors=True)

    success = job.status == Status.Completed
    return success, f"run finished as {job.status}\n{job.logs}"
