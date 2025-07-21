import asyncio
import logging
import os
import re
import shlex
import textwrap
import zipfile
from pathlib import Path
from typing import Any

import aiohttp
from lightning_sdk import Job, Machine, Status, Studio

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


async def download_repo_archive(
    repo_owner: str, repo_name: str, ref: str, token: str, folder_path: str | Path, suffix: str = ""
) -> Path:
    """Download a GitHub repository archive at a specific ref (branch, tag, commit) and return the path."""
    # Fetch zipball archive
    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/zipball/{ref}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    archive_name = f"{repo_owner}-{repo_name}-{ref}"
    if suffix:
        archive_name = f"{archive_name}-{suffix}"
    folder_path = Path(folder_path).resolve()
    folder_path.mkdir(parents=True, exist_ok=True)
    archive_path = folder_path / f"{archive_name}.zip"

    logging.debug(f"Pull repo from {url}")
    async with aiohttp.ClientSession() as session, session.get(url, headers=headers) as resp:
        resp.raise_for_status()
        archive_data = await resp.read()

    # Save archive to file
    with open(archive_path, "wb") as f:
        f.write(archive_data)

    return archive_path


def extract_zip_archive(zip_path: Path, extract_to: Path, subfolder: str = "") -> Path:
    """Extract a zip archive to a specified directory, optionally filtering by subfolder."""
    if not zip_path.is_file():
        raise FileNotFoundError(f"Zip file {zip_path} does not exist.")

    extract_to.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        first_path = zf.namelist()[0]  # e.g. "repo-owner-repo-sha1234abcd/"
        root_folder = first_path.split("/", 1)[0]
        if subfolder:
            for file_info in zf.infolist():
                fname = "/".join(file_info.filename.split("/")[1:])
                if fname.startswith(f"{subfolder}/"):
                    zf.extract(file_info, extract_to)
        else:
            zf.extractall(extract_to)

    return (extract_to / root_folder).resolve()  # Return the path to the extracted repo folder


async def download_repo_and_extract(
    repo_owner: str,
    repo_name: str,
    ref: str,
    token: str,
    folder_path: str | Path,
    suffix: str = "",
    subfolder: str = "",
) -> Path:
    """Download a GitHub repository at a specific ref (branch, tag, commit) and extract it to a temp directory."""
    folder_path = Path(folder_path).resolve()
    folder_path.mkdir(parents=True, exist_ok=True)

    # 1) Download zipball archive
    archive_path = await download_repo_archive(
        repo_owner=repo_owner, repo_name=repo_name, ref=ref, token=token, folder_path=folder_path, suffix=suffix
    )

    # 2) Extract zip into a temp directory
    path_repo = extract_zip_archive(zip_path=archive_path, extract_to=folder_path, subfolder=subfolder)

    return path_repo.resolve()


async def run_repo_job(
    cfg_file_name: str, config: dict, params: dict, repo_dir: str | Path, repo_archive: str | Path, job_name: str
) -> tuple[Job, str]:
    """Download the full repo at `ref` into a tempdir, look for config and execute the job."""
    # mandatory
    config_run = config["run"]
    # optional
    docker_run_machine = Machine.from_str(params.get("machine") or config.get("machine", "CPU"))
    docker_run_image = params.get("image") or config.get("image", "ubuntu:22.04")
    config_env = config.get("env", {})
    config_env.update(params)  # add params to env

    this_studio = Studio()
    this_teamspace = this_studio.teamspace

    # check if the repo_dir is a valid path
    docker_run_script = f".lightning_workflow_{cfg_file_name.split('.')[0]}-{generate_unique_hash(params=params)}.sh"
    cmd_path = os.path.join(repo_dir, docker_run_script)
    assert not os.path.isfile(cmd_path), "the expected actions script already exists"
    # dump the cmd to .lightning/actions.sh
    with open(cmd_path, "w", encoding="utf_8") as fp:
        fp.write(config_run + os.linesep)
    assert os.path.isfile(cmd_path), "missing the created actions script"
    # await asyncio.sleep(60)  # todo: wait for the file to be written, likely Job sync issue

    # prepare the environment variables to export
    export_envs = "\n".join([f"export {k}={shlex.quote(str(v))}" for k, v in config_env.items()])

    # 1) List the commands you want to run inside the box
    cutoff_str = ("%" * 15) + f" CUT LOG {generate_unique_hash(32)} " + ("%" * 15)
    debug_cmds = ["printenv", "cp -r /temp_repo/. /workspace/", "ls -lah", f"cat {docker_run_script}"]

    # 2) Prefix each with `box "<cmd>"`
    boxed_cmds = "\n".join(f'box "{cmd}"' for cmd in debug_cmds)

    # 3) Build the full Docker‐run call using a heredoc
    with_gpus = "" if docker_run_machine.is_cpu() else "--gpus=all"
    lit_download_args = " ".join([
        f"--studio={this_studio.name}",
        f"--teamspace={this_teamspace.owner.name}/{this_teamspace.name}",
        "--local-path=/temp_repo",
    ])
    local_repo_archive = Path(repo_archive).relative_to("/teamspace/studios/this_studio/")
    local_bash_script = Path(cmd_path).relative_to("/teamspace/studios/this_studio/")
    job_cmd = (
        "mkdir -p /temp_repo && "
        # download the repo archive to /temp_repo
        f"lightning download file {local_repo_archive} {lit_download_args} && "
        f"lightning download file {local_bash_script} {lit_download_args} && "
        f"ls -lah /temp_repo && "
        # continue with the real docker run
        " docker run --rm -i"
        f" -v /temp_repo:/temp_repo"
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
        interruptible=to_bool(config.get("interruptible", True)),
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
