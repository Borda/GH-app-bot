import asyncio
import io
import logging
import os
import re
import shlex
import textwrap
import zipfile
from pathlib import Path
from typing import Any

import aiohttp
from lightning_sdk import Job, Machine, Status

from py_bot.utils import generate_unique_hash

LOCAL_ROOT_DIR = Path(__file__).parent
PROJECT_ROOT_DIR = LOCAL_ROOT_DIR.parent
LOCAL_TEMP_DIR = LOCAL_ROOT_DIR / ".temp"
BASH_BOX_FUNC = textwrap.dedent("""\
  box() {
    local cmd="$1"
    local tmp;  tmp=$(mktemp)
    local max=0
    local line
    while IFS= read -r line; do
      echo "$line" >> "$tmp"
      local len=${#line}
      (( len > max )) && max=$len
    done < <(eval "$cmd" 2>&1)

    local border; border=$(printf '%*s' "$max" '' | tr ' ' '-')
    printf "+%s+\\n" "$border"
    while IFS= read -r l; do
      printf "| %-${max}s |\\n" "$l"
    done < "$tmp"
    printf "+%s+\\n" "$border"
    rm "$tmp"
  }
""")

ANSI_ESCAPE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub("", text)


async def run_sleeping_task(*args: Any, **kwargs: Any):
    # Replace it with real logic; here we just succeed
    await asyncio.sleep(60)
    return True


async def download_repo_and_extract(repo_owner: str, repo_name: str, ref: str, token: str, suffix: str = "") -> Path:
    """Download a GitHub repository at a specific ref (branch, tag, commit) and extract it to a temp directory."""
    # 1) Fetch zipball archive
    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/zipball/{ref}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    async with aiohttp.ClientSession() as session, session.get(url, headers=headers) as resp:
        resp.raise_for_status()
        archive_data = await resp.read()
    logging.info(f"Pull repo from {url}")

    # 2) Extract zip into a temp directory
    tempdir = LOCAL_TEMP_DIR.resolve()
    tempdir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(archive_data)) as zf:
        # 1) Grab the first entry in the archive’s name list
        first_path = zf.namelist()[0]  # e.g. "repo-owner-repo-sha1234abcd/"
        root_folder = first_path.split("/", 1)[0]
        # 2) Extract everything
        zf.extractall(tempdir)

    # 3) rename the extracted folder if a suffix is provided
    path_repo = tempdir / root_folder
    if suffix:
        new_path_repo = tempdir / f"{root_folder}-{suffix}"
        if new_path_repo.exists():
            raise IsADirectoryError(f"Path {new_path_repo} already exists, cannot rename {path_repo}")
        path_repo.rename(new_path_repo)
        path_repo = new_path_repo

    return path_repo


async def run_repo_job(
    cfg_file_name: str, config: dict, params: dict, repo_dir: str | Path, job_name: str
) -> tuple[Job, str]:
    """Download the full repo at `ref` into a tempdir, look for config and execute the job."""
    # mandatory
    config_run = config["run"]
    # optional
    docker_run_machine = Machine.from_str(params.get("machine") or config.get("machine", "CPU"))
    docker_run_image = params.get("image") or config.get("image", "ubuntu:22.04")
    config_env = config.get("env", {})
    config_env.update(params)  # add params to env
    # print(f"CMD: {cmd_run}")
    docker_run_script = f".lightning_workflow_{cfg_file_name.split('.')[0]}-{generate_unique_hash(params=params)}.sh"
    cmd_path = os.path.join(repo_dir, docker_run_script)
    assert not os.path.isfile(cmd_path), "the expected actions script already exists"
    # dump the cmd to .lightning/actions.sh
    with open(cmd_path, "w", encoding="utf_8") as fp:
        fp.write(config_run + os.linesep)
    assert os.path.isfile(cmd_path), "missing the created actions script"
    await asyncio.sleep(5)  # todo: wait for the file to be written, likely Job sync issue
    export_envs = "\n".join([f"export {k}={shlex.quote(str(v))}" for k, v in config_env.items()])
    # 1) List the commands you want to run inside the box
    cutoff_str = ("%" * 15) + f" CUT LOG {generate_unique_hash(32)} " + ("%" * 15)
    debug_cmds = ["printenv", "cp -r /temp_repo/. /workspace/", "ls -lah", f"cat {docker_run_script}"]
    # 2) Prefix each with `box "<cmd>"`
    boxed_cmds = "\n".join(f'box "{cmd}"' for cmd in debug_cmds)
    # 3) Build the full Docker‐run call using a heredoc
    with_gpus = "" if docker_run_machine.is_cpu() else "--gpus=all"
    job_cmd = (
        # early check that executable bash is available
        f"if [ ! -e {repo_dir}/{docker_run_script} ]; then"
        f" rm -rf {PROJECT_ROOT_DIR}/.git; " # todo: consider remove all .git folders
        " python -m py_tree -s -d 4; exit 1; "  # depth 4 is to show top-level of the .temp/repo
        "fi;"
        # continue with the real docker run
        " docker run --rm -i"
        f" -v {repo_dir}:/temp_repo"
        " -w /workspace"
        f" {with_gpus} {docker_run_image}"
        # Define your box() helper as a Bash function
        " bash -s << 'EOF'\n"
        f"{export_envs}\n"
        f"{BASH_BOX_FUNC}\n"
        f"{boxed_cmds}\n"
        f'echo "{cutoff_str}"\n'
        f"bash {docker_run_script}\n"
        "EOF"
    )
    logging.debug(f"job >> {job_cmd}")
    # 4) Run the job with the Job.run() method
    job = Job.run(
        name=job_name,
        command=job_cmd,
        machine=docker_run_machine,
        interruptible=config.get("interruptible", False),  # fixme: loaded as string, convert to bool
    )
    return job, cutoff_str


def finalize_job(job: Job, cutoff_str: str, debug: bool = False) -> tuple[bool, str]:
    """Finalize the job by updating its status and logs."""
    success = job.status == Status.Completed
    logs = strip_ansi(job.logs or "No logs available")
    if debug or not cutoff_str:
        return success, logs
    # in non-debug mode, we cut the logs to avoid too much output
    # we expect the logs to contain the cutoff string twice
    for it in range(2):
        # cut the logs all before the cutoff string
        cutoff_index = logs.find(cutoff_str)
        if cutoff_index == -1:
            logging.warn(f"iter {it}: the cutoff string was not found in the logs")
        logs = logs[cutoff_index + len(cutoff_str) :]

    # todo: cleanup job if needed or success
    return success, logs
