import asyncio
import hashlib
import os
import re
import time
import zipfile
from pathlib import Path
from textwrap import TextWrapper
from typing import Any

import jwt
from aiohttp import client_exceptions


def generate_unique_hash(length=16, params: dict | None = None) -> str:
    """Generate a unique hash string of a specified length.

    >>> hash_16 = generate_unique_hash(params={"test": "value"})
    >>> len(hash_16)
    16
    """
    # Use the current timestamp and a counter for uniqueness
    unique_string = f"{time.time()}{os.getpid()}{params.values() if params else ''}"
    hash_object = hashlib.md5(unique_string.encode())
    return hash_object.hexdigest()[:length]


def to_bool(value) -> bool:
    """Convert various boolean indicators to a Python bool.

    Supports:
    - bool: returned as is
    - int: 1 (True) or 0 (False)
    - str: case-insensitive 'true', 'false', '1', '0', 'yes', 'no', 'on', 'off'

    >>> to_bool(True)
    True
    >>> to_bool('true')
    True
    >>> to_bool('FALSE')
    False
    >>> to_bool(1)
    True
    >>> to_bool(0)
    False
    >>> to_bool('yes')
    True
    >>> to_bool('No')
    False
    >>> to_bool('invalid')
    Traceback (most recent call last):
      ...
    ValueError: Unrecognized boolean value: 'invalid'
    """
    if value is None:
        return False

    if isinstance(value, bool):
        return value

    if isinstance(value, int):
        if value in (0, 1):
            return bool(value)
        raise ValueError(f"Unrecognized boolean int: {value}")

    if isinstance(value, str):
        val = value.strip().lower()
        truthy = {"true", "1", "yes", "y", "on"}
        falsy = {"false", "0", "no", "n", "off"}
        if val in truthy:
            return True
        if val in falsy:
            return False
        raise ValueError(f"Unrecognized boolean value: {value!r}")

    raise ValueError(f"Type {type(value).__name__} is not supported for boolean conversion")


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
                # Remove the first path component (e.g., the root folder) from the file path
                fname = Path(*Path(file_info.filename).parts[1:])
                if fname.as_posix().startswith(f"{subfolder}/"):
                    zf.extract(file_info, extract_to)
        else:
            zf.extractall(extract_to)

    return (extract_to / root_folder).resolve()  # Return the path to the extracted repo folder


def wrap_long_text(text: str, line_width: int = 200, text_length: int = 65525) -> str:
    """Wrap long lines in the text to a specified width, using '>' as the continuation symbol.

    >>> my_text = '''Well, well, well, what do we have here?
    ... This is a very long line that should be wrapped to fit within N characters.
    ... It should also handle multiple lines correctly.
    ... This is another line will be truncated as it exceeds the specified text length limit.
    ... '''
    >>> print(wrap_long_text(my_text, line_width=56, text_length=150))
    Well, well, well, what do we have here?
    This is a very long line that should be wrapped to fit
    ↪ within N characters.
    …(truncated)
    """
    wrapper = TextWrapper(
        width=line_width,
        replace_whitespace=False,
        subsequent_indent="↪ ",  # Unicode character for right arrow with hook (↪)
    )
    lines = [wrapper.fill(line) for line in text.splitlines()]
    text_wrapped = "\n".join(lines)
    if len(text_wrapped) > text_length:
        text_wrapped = text_wrapped[:text_length]
        lines_truncated = text_wrapped.splitlines()
        text_wrapped = "\n".join(lines_truncated[:-1] + ["…(truncated)"])
    return text_wrapped


def sanitize_params_for_env(params: dict) -> dict:
    """Sanitize parameters for use in environment variables.

    Replaces any character that is not a letter, number, or underscore with an underscore.

    >>> sanitize_params_for_env({"key 1": "value1", "key-2": "value2"})
    {'key_1': 'value1', 'key_2': 'value2'}
    """
    sanitized_params = {}
    for key, value in params.items():
        if not isinstance(key, str):
            raise ValueError(f"Parameter keys must be strings, got {type(key).__name__}.")
        sanitized_key = re.sub(r"\W", "_", key)
        sanitized_params[sanitized_key] = value
    return sanitized_params


def _load_validate_required_env_vars() -> tuple[int, str, str]:
    """Ensure required environment variables are set."""
    github_app_id = os.getenv("GITHUB_APP_ID")
    assert github_app_id, "`GITHUB_APP_ID` must be set in environment variables"
    private_key = os.getenv("PRIVATE_KEY")
    if not private_key:
        # If PRIVATE_KEY is not set, read from a file specified by PRIVATE_KEY_PATH
        private_key_path = os.getenv("PRIVATE_KEY_PATH")
        assert private_key_path, "`PRIVATE_KEY_PATH` must be set in environment variables"
        private_key_path = Path(private_key_path).expanduser().resolve()
        assert private_key_path.is_file(), f"Private key file not found at {private_key_path}"
        private_key = private_key_path.read_text()
    webhook_secret = os.getenv("WEBHOOK_SECRET", "")
    return int(github_app_id), private_key, webhook_secret


def create_jwt_token(github_app_id: int, app_private_key: str) -> str:
    """Create a JWT token for authenticating with the GitHub API."""
    return jwt.encode(
        {"iat": int(time.time()) - 60, "exp": int(time.time()) + (10 * 60), "iss": github_app_id},
        app_private_key,
        algorithm="RS256",
    )


def extract_repo_details(event_type: str, payload: dict) -> tuple[str, str, str, str]:
    """Extract the repository owner, name, head SHA, and branch ref from the event payload."""
    if event_type == "push":
        head_sha = payload["after"]
        branch_ref = payload["ref"][len("refs/heads/") :]
    elif event_type == "pull_request":
        head_sha = payload["pull_request"]["head"]["sha"]
        branch_ref = payload["pull_request"]["base"]["ref"]
    else:
        raise ValueError(f"Unsupported event type: {event_type}")
    repo_owner = payload["repository"]["owner"]["login"]
    repo_name = payload["repository"]["name"]
    return repo_owner, repo_name, head_sha, branch_ref


async def post_with_retry(gh, url: str, data: dict, retries: int = 3, backoff: float = 1.0) -> Any:
    """Post data to GitHub API with retries in case of connection issues."""
    for it in range(1, retries + 1):
        try:
            return await gh.post(url, data=data)
        except client_exceptions.ServerDisconnectedError:
            if it == retries:
                raise
            await asyncio.sleep(backoff * it)
    return None


async def patch_with_retry(gh, url: str, data: dict, retries: int = 3, backoff: float = 1.0) -> Any:
    """Post data to GitHub API with retries in case of connection issues."""
    for it in range(1, retries + 1):
        try:
            return await gh.patch(url, data=data)
        except client_exceptions.ServerDisconnectedError:
            if it == retries:
                raise
            await asyncio.sleep(backoff * it)
    return None
