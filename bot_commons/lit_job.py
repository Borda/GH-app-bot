import asyncio
import logging
import re
import shlex
import textwrap
from typing import Any

from lightning_sdk import Job, Machine, Status

from bot_async_tasks.downloads import _RELATIVE_PATH_DOWNLOAD
from bot_commons.configs import ConfigRun
from bot_commons.utils import generate_unique_hash

BASH_BOX_FUNC = textwrap.dedent("""\
box(){
  cmd="$1"
  tmp=$(mktemp)
  max=0
  while IFS= read -r line; do
    echo "$line" >> "$tmp"
    (( ${#line} > max )) && max=${#line}
  done < <(eval "$cmd" 2>&1)
  border=$(printf '%*s' "$max" '' | tr ' ' '-')
  printf "+%s+\\n" "$border"
  while IFS= read -r l; do
    printf "| %-${max}s |\\n" "$l"
  done < "$tmp"
  printf "+%s+\\n" "$border"
  rm "$tmp"
}
""")
ANSI_ESCAPE = re.compile(r"\x1B\[[0-?]*[ -/]*[@-~]")


def _generate_script_content(export_envs, config_run, separator_str):
    return textwrap.dedent(f"""#!/bin/bash
{export_envs}
printenv
ls -lah
echo "{separator_str}"
{config_run}
    """)


def strip_ansi(text: str) -> str:
    return ANSI_ESCAPE.sub("", text)


async def run_sleeping_task(*args: Any, **kwargs: Any):
    # Replace it with real logic; here we just succeed
    await asyncio.sleep(60)
    return True


async def run_repo_job(cfg_file_name: str, config: ConfigRun, token: str, job_name: str) -> tuple[Job, str, str]:
    """Download the full repo at `ref` into a tempdir, look for config and execute the job."""
    # mandatory
    assert config.run
    # optional
    docker_run_machine = Machine.from_str(config.machine)

    # prepare the environment variables to export
    export_envs = "\n".join([f"export {k}={shlex.quote(str(v))}" for k, v in config.env.items()])

    # 1) List the commands you want to run inside the box
    job_hash = generate_unique_hash(16, params=config.params)
    logs_hash = ("%" * 20) + f"_RUN-LOGS-{generate_unique_hash(32)}_" + ("%" * 20)
    exit_hash = ("%" * 20) + f"_EXIT-CODE-{generate_unique_hash(32)}_" + ("%" * 20)

    # 2) generate the script content
    script_file = f"{cfg_file_name.replace('.', '_')}_{job_hash}.sh"
    script_content = _generate_script_content(export_envs=export_envs, config_run=config.run, separator_str=logs_hash)

    # 3) Build the full Docker‐run call using a heredoc
    with_gpus = "" if docker_run_machine.is_cpu() else "--gpus=all"
    temp_repo_folder = "temp_repo"
    job_cmd = (
        "set -e ; "
        "printenv ; "
        f"python {_RELATIVE_PATH_DOWNLOAD} ; "
        f"PATH_REPO_TEMP=$(realpath {temp_repo_folder}) ; "
        f"cat > $PATH_REPO_TEMP/{script_file} << 'EOF' ;\n"
        f"{script_content}\n"
        "EOF\n"
        f"chmod +x $PATH_REPO_TEMP/{script_file} ; "
        "ls -lah $PATH_REPO_TEMP ; "
        "rc=0 ; "
        "docker run --rm -i"
        " -v ${PATH_REPO_TEMP}:/workspace"
        " -w /workspace"
        f" {with_gpus} {config.image}"
        f" bash -eo pipefail {script_file} || rc=$? ; "
        f'echo "{exit_hash}\n$rc\n{exit_hash}" ; '
    )
    logging.debug(f"job >> {job_cmd}")

    # 4) Run the job with the Job.run() method
    job = Job.run(
        name=job_name,
        command=job_cmd,
        machine=docker_run_machine,
        interruptible=config.interruptible,
        env={
            "LIGHTNING_DEBUG": "1",
            "GITHUB_REPOSITORY_OWNER": config.repository_owner,
            "GITHUB_REPOSITORY_NAME": config.repository_name,
            "GITHUB_REPOSITORY_REF": config.repository_ref,
            "GITHUB_TOKEN": token,
            "PATH_REPO_FOLDER": temp_repo_folder,
        },
    )
    return job, logs_hash, exit_hash


def finalize_job(job: Job, logs_hash: str, exit_hash: str, debug: bool = False) -> tuple[Status, int | None, str]:
    """Finalize the job by updating its status and logs."""
    logs = strip_ansi(job.logs or "No logs available")
    search_exit_code = re.search(rf"{exit_hash}\n(\d+)\n{exit_hash}", logs)
    exit_code = int(search_exit_code.group(1)) if search_exit_code else None
    if debug or not logs_hash:
        return job.status, exit_code, logs
    # in non-debug mode, we cut the logs to avoid too much output
    # we expect the logs to contain the cutoff string twice
    for it in range(2):
        # cut the logs all before the cutoff string
        cutoff_index = logs.find(logs_hash)
        if cutoff_index == -1:
            logging.warn(f"iter {it}: the cutoff string was not found in the logs")
        logs = logs[cutoff_index + len(logs_hash) :]

    # todo: cleanup job if needed or success
    return job.status, exit_code, logs
