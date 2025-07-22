import asyncio
import logging
import re
import shlex
import textwrap
from typing import Any

from lightning_sdk import Job, Machine, Status

from py_bot.utils import generate_unique_hash, to_bool

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


async def run_repo_job(cfg_file_name: str, config: dict, params: dict, token: str, job_name: str) -> tuple[Job, str]:
    """Download the full repo at `ref` into a tempdir, look for config and execute the job."""
    # mandatory
    config_run = config["run"]
    # optional
    docker_run_machine = Machine.from_str(params.get("machine") or config.get("machine", "CPU"))
    docker_run_image = params.get("image") or config.get("image", "ubuntu:22.04")
    config_env = config.get("env", {})
    config_env.update(params)  # add params to env

    # prepare the environment variables to export
    export_envs = "\n".join([f"export {k}={shlex.quote(str(v))}" for k, v in config_env.items()])

    # 1) List the commands you want to run inside the box
    cutoff_str = ("%" * 15) + f" CUT LOG {generate_unique_hash(32)} " + ("%" * 15)
    docker_debug_cmds = ["printenv", "set -ex", "ls -lah"]

    # 2) Prefix each with `box "<cmd>"`
    boxed_cmds = "\n".join(f'box "{cmd}"' for cmd in docker_debug_cmds)

    # 3) Build the full Docker‐run call using a heredoc
    with_gpus = "" if docker_run_machine.is_cpu() else "--gpus=all"
    temp_repo_folder = "temp_repo"
    job_cmd = (
        "printenv && "
        # download the repo to temp_repo
        "python GH-app-bot/py_bot/downloads.py && "
        f"PATH_REPO_TEMP=$(realpath {temp_repo_folder}) && "
        # "pip install -q py-tree && "
        # "python -m py_tree -s -d 3 && "
        "ls -lah $PATH_REPO_TEMP && "
        # continue with the real docker run
        "docker run --rm -i"
        " -v ${PATH_REPO_TEMP}:/workspace"
        " -w /workspace"
        f" {with_gpus} {docker_run_image}"
        # Define your box() helper as a Bash function
        " bash -s << 'EOF'\n"
        f"{export_envs}\n"
        f"{BASH_BOX_FUNC}\n"
        f"{boxed_cmds}\n"
        f'echo "{cutoff_str}"\n'
        f"{config_run}\n"
        "EOF"
    )
    logging.debug(f"job >> {job_cmd}")

    # 4) Run the job with the Job.run() method
    job = Job.run(
        name=job_name,
        command=job_cmd,
        machine=docker_run_machine,
        interruptible=to_bool(config.get("interruptible", True)),
        env={
            "LIGHTNING_DEBUG": "1",
            "GITHUB_REPOSITORY_OWNER": config.get("repository_owner", ""),
            "GITHUB_REPOSITORY_NAME": config.get("repository_name", ""),
            "GITHUB_REPOSITORY_REF": config.get("repository_ref", ""),
            "GITHUB_TOKEN": token,
            "PATH_REPO_FOLDER": temp_repo_folder,
        },
    )
    return job, cutoff_str


def finalize_job(job: Job, cutoff_str: str, debug: bool = False) -> tuple[Status, str]:
    """Finalize the job by updating its status and logs."""
    logs = strip_ansi(job.logs or "No logs available")
    if debug or not cutoff_str:
        return job.status, logs
    # in non-debug mode, we cut the logs to avoid too much output
    # we expect the logs to contain the cutoff string twice
    for it in range(2):
        # cut the logs all before the cutoff string
        cutoff_index = logs.find(cutoff_str)
        if cutoff_index == -1:
            logging.warn(f"iter {it}: the cutoff string was not found in the logs")
        logs = logs[cutoff_index + len(cutoff_str) :]

    # todo: cleanup job if needed or success
    return job.status, logs
