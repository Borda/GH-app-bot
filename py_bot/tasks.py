import asyncio
import io
import os
import zipfile
from pathlib import Path

import aiohttp
from lightning_sdk import Job, Machine, Status

from py_bot.utils import generate_unique_hash

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


async def job_await(job, interval: float = 5.0, timeout=None) -> None:
    # todo: temp solution until job has async wait method
    start = asyncio.get_event_loop().time()
    while True:
        if job.status in (Status.Completed, Status.Stopped, Status.Failed):
            break

        if timeout is not None and asyncio.get_event_loop().time() - start > timeout:
            raise TimeoutError("Job didn't finish within the provided timeout.")

        await asyncio.sleep(interval)


async def run_repo_job(config: dict, params: dict, repo_dir: str, job_name: str):
    """Download the full repo at `ref` into a tempdir, look for config and execute the job."""
    # mandatory
    config_run = config["run"]
    # optional
    docker_run_machine = Machine.from_str(params.get("machine") or config.get("machine", "CPU"))
    docker_run_image = params.get("image") or config.get("image", "ubuntu:22.04")
    config_env = config.get("env", {})
    config_env.update(params)  # add params to env

    cmd_run = os.linesep.join(["ls -lah", config_run])
    # print(f"CMD: {cmd_run}")
    docker_run_script = f".lightning-actions-{generate_unique_hash()}.sh"
    cmd_path = os.path.join(repo_dir, docker_run_script)
    assert not os.path.isfile(cmd_path), "the expected actions script already exists"
    # dump the cmd to .lightning/actions.sh
    with open(cmd_path, "w", encoding="utf_8") as fp:
        fp.write(cmd_run + "\n")
        fp.flush()
        os.fsync(fp.fileno())  # Ensure the file is flushed and synced to disk
    assert os.path.isfile(cmd_path), "missing the created actions script"
    import shlex
    docker_run_env = " ".join([f'-e {k}={shlex.quote(str(v))}' for k, v in config_env.items()])
    # at the beginning make copy of the repo_dir to avoid conflicts with other jobs
    docker_run_cmd = " && ".join([
        "printenv",
        "cp -r /temp_repo/. /workspace/",
        "ls -lah",
        f"cat {docker_run_script}",
        f"bash {docker_run_script}",
    ])
    job_cmd = (
        "docker run --rm"
        f" -v {repo_dir}:/temp_repo"
        " -w /workspace"
        f" {docker_run_env} {docker_run_image}"
        f" bash -lc '{docker_run_cmd}'"
    )
    print(f"job >> {job_cmd}")
    job = Job.run(
        name=job_name,
        command=job_cmd,
        machine=docker_run_machine,
        interruptible=config.get("interruptible", False),
    )
    await job_await(job)

    success = job.status == Status.Completed
    # todo: cleanup job if needed or success
    return success, f"run finished as {job.status}\n{job.logs}"
