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


async def run_sleeping_task(*args, **kwargs):
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


async def job_await(job, interval: float = 5.0, timeout = None) -> None:
    # todo: temp solution until jib has async wait method
    import asyncio

    from lightning_sdk.status import Status

    start = asyncio.get_event_loop().time()
    while True:
        if job.status in (Status.Completed, Status.Stopped, Status.Failed):
            break

        if timeout is not None and asyncio.get_event_loop().time() - start > timeout:
            raise TimeoutError("Job didn't finish within the provided timeout.")

        await asyncio.sleep(interval)


async def run_repo_job(repo_dir: str, job_name: str):
    """Download the full repo at `ref` into a tempdir, look for config and execute the job."""
    # repo_root = await _download_repo_and_extract(owner, repo, ref, token)
    # if not repo_root:
    #     return False, f"Failed to download or extract repo {owner}/{repo} at {ref}"

    # Try to load the config file
    cfg_path = os.path.join(repo_dir, ".lightning/actions.yaml")
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
    cmd_path = os.path.join(repo_dir, docker_run_script)
    # assert not os.path.isfile(cmd_path), "the expected actions.sh file already exists"  # todo: add unique hash
    # dump the cmd to .lightning/actions.sh
    with open(cmd_path, "w", encoding="utf_8") as fp:
        fp.write(cmd_run + "\n")
    docker_run_env = " ".join([f'-e {k}="{v}"' for k, v in config_env.items()])
    job_cmd = (
        "docker run --rm "
        f"-v {repo_dir}:/workspace "
        "-w /workspace "
        f"{docker_run_env} "
        f"{docker_run_image} "
        f"bash -lc 'ls -lah && cat {docker_run_script} && bash {docker_run_script}'"
    )
    print(f"job >> {job_cmd}")
    job = Job.run(
        name=job_name,
        command=job_cmd,
        machine=Machine.CPU,
        # interruptible=True,
    )
    await job_await(job)
    # shutil.rmtree(os.path.dirname(repo_dir), ignore_errors=True)

    success = job.status == Status.Completed
    return success, f"run finished as {job.status}\n{job.logs}"
