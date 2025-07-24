import io
import logging
import os
import zipfile
from pathlib import Path

import aiohttp

_PATH_DOWNLOAD_PARTS = Path(__file__).resolve().parts
# extracting the relative path as GH-app-bot/py_bot/downloads.py
_RELATIVE_PATH_DOWNLOAD = os.path.join(*_PATH_DOWNLOAD_PARTS[-3:])


async def download_repo_archive(
    repo_owner: str, repo_name: str, git_ref: str, token: str, folder_path: str | Path, suffix: str = ""
) -> Path:
    """Download a GitHub repository archive at a specific ref (branch, tag, commit) and return the path."""
    # Fetch zipball archive
    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/zipball/{git_ref}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    archive_name = f"{repo_owner}-{repo_name}-{git_ref}"
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


async def download_repo_and_extract(
    repo_owner: str,
    repo_name: str,
    git_ref: str,
    token: str,
    folder_path: str | Path,
    subfolder: str = "",
    suffix: str = "",
) -> Path | None:
    """Download a GitHub repository at a specific ref (branch, tag, commit) and extract it to a temp directory."""
    # 1) Fetch zipball archive
    url = f"https://api.github.com/repos/{repo_owner}/{repo_name}/zipball/{git_ref}"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    async with aiohttp.ClientSession() as session, session.get(url, headers=headers) as resp:
        resp.raise_for_status()
        archive_data = await resp.read()
    logging.debug(f"Pull repo from {url}")

    # 2) Extract zip into a temp directory
    folder_path = Path(folder_path).resolve()
    folder_path.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(archive_data)) as zf:
        # Grab the first entry in the archive’s name list
        first_path = zf.namelist()[0]  # e.g. "repo-owner-repo-sha1234abcd/"
        root_folder = first_path.split("/", 1)[0]
        if subfolder:  # Extract only files in the specified subfolder
            for file_info in zf.infolist():
                # Remove the first path component (e.g., the root folder) from the file path
                fname = Path(*Path(file_info.filename).parts[1:])
                if not fname.parts:  # Skip if fname is empty
                    continue
                if fname.as_posix().startswith(f"{subfolder}/"):
                    zf.extract(file_info, folder_path)
        else:  # Extract everything
            zf.extractall(folder_path)

    # 3) rename the extracted folder if a suffix is provided
    path_repo = folder_path / root_folder
    if not path_repo.exists():
        return None
    if suffix:
        new_path_repo = folder_path / f"{root_folder}-{suffix}"
        if new_path_repo.exists():
            raise FileExistsError(f"Path {new_path_repo} already exists, cannot rename {path_repo}")
        path_repo.rename(new_path_repo)
        path_repo = new_path_repo

    return path_repo


async def cli_download_repo_and_extract() -> None:
    """CLI entry point to download and extract a GitHub repository."""
    import shutil
    import tempfile

    repo_owner = os.getenv("GITHUB_REPOSITORY_OWNER")
    assert repo_owner, "`GITHUB_REPOSITORY_OWNER` environment variable is not set"
    repo_name = os.getenv("GITHUB_REPOSITORY_NAME")
    assert repo_name, "`GITHUB_REPOSITORY_NAME` environment variable is not set"
    repo_ref = os.getenv("GITHUB_REPOSITORY_REF")
    assert repo_ref, "`GITHUB_REPOSITORY_REF` environment variable is not set"
    token = os.getenv("GITHUB_TOKEN")
    assert token, "`GITHUB_TOKEN` environment variable is not set"
    path_folder = os.getenv("PATH_REPO_FOLDER")
    assert path_folder, "`PATH_REPO_FOLDER` environment variable is not set"

    temp_dir = Path(tempfile.gettempdir()).resolve()
    temp_dir.mkdir(parents=True, exist_ok=True)
    # Download and extract the repository
    print(f"Downloading repository {repo_owner}/{repo_name} at ref {repo_ref}")
    repo_path = await download_repo_and_extract(
        repo_owner=repo_owner,
        repo_name=repo_name,
        git_ref=repo_ref,
        token=token,
        folder_path=temp_dir,
    )
    if not repo_path:
        raise FileNotFoundError(f"Failed to download or extract repository {repo_owner}/{repo_name} at ref {repo_ref}")
    print(f"Repository downloaded and extracted to {repo_path}")
    # move the extracted folder to the workspace
    # Ensure the destination folder exists
    path_folder = Path(path_folder).resolve()
    path_folder.mkdir(parents=True, exist_ok=True)

    # Move each subdirectory or file, handling name conflicts
    for sub_dir in repo_path.iterdir():
        destination = path_folder / sub_dir.name
        if destination.exists():
            raise FileExistsError(f"Destination {destination} already exists. Cannot move {sub_dir}.")
        shutil.move(str(sub_dir), str(destination))
    print(f"Moved repository to {path_folder}")


if __name__ == "__main__":
    import asyncio

    asyncio.run(cli_download_repo_and_extract())
