import hashlib
import logging
import logging.handlers
import os
import re
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from textwrap import TextWrapper
from typing import Any


def generate_unique_hash(length: int = 16, params: dict | None = None) -> str:
    """Generate a unique hash string of a specified length.

    Args:
        length: Number of hex characters to return.
        params: Optional parameters included to diversify the hash.

    Returns:
        A hexadecimal string of the given length.

    Examples:
        >>> hash_16 = generate_unique_hash(params={"test": "value"})
        >>> len(hash_16)
        16
    """
    # Use the current timestamp and a counter for uniqueness
    unique_string = f"{time.time()}{os.getpid()}{params.values() if params else ''}"
    hash_object = hashlib.md5(unique_string.encode())
    return hash_object.hexdigest()[:length]


def to_bool(value: Any) -> bool:
    """Convert common boolean-like values to a Python bool.

    Supports:
    - bool: returned as is
    - int: 1 (True) or 0 (False)
    - str: case-insensitive 'true', 'false', '1', '0', 'yes', 'no', 'on', 'off'

    Args:
        value: The value to convert.

    Returns:
        The converted boolean.

    Raises:
        ValueError: If the value cannot be interpreted as boolean.

    Examples:
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
    """Extract a zip archive to a specified directory, optionally filtering by subfolder.

    Args:
        zip_path: Path to the .zip archive.
        extract_to: Destination directory.
        subfolder: Optional subfolder prefix to filter extracted files.

    Returns:
        Path to the extracted root folder.

    Raises:
        FileNotFoundError: If the archive does not exist.
        OSError: If extraction fails.
    """
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
    """Wrap lines and optionally truncate output to a maximum length.

    Args:
        text: Input text to wrap.
        line_width: Maximum line width before wrapping.
        text_length: Maximum total length of the returned text.

    Returns:
        The wrapped (and possibly truncated) text.

    Examples:
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

    Args:
        params: Input dictionary with arbitrary keys and values.

    Returns:
        A dictionary with keys converted to env-safe names.

    Raises:
        ValueError: If a key is not a string.

    Examples:
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


def load_validate_required_env_vars() -> tuple[int, str, str]:
    """Ensure required environment variables are set.

    Returns:
        A tuple of (github_app_id, private_key, webhook_secret).

    Raises:
        AssertionError: If a required environment variable is missing.
        AssertionError: If PRIVATE_KEY_PATH is provided but the file is missing.
    """
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


def extract_repo_details(event_type: str, payload: dict) -> tuple[str, str, str, str]:
    """Extract repository owner, name, head SHA, and branch ref from the payload.

    Args:
        event_type: GitHub webhook event type ("push", "pull_request", ...).
        payload: Raw event payload dictionary.

    Returns:
        A tuple of (repo_owner, repo_name, head_sha, branch_ref).

    Raises:
        ValueError: If the event type is unsupported.
    """
    if event_type == "push":
        head_sha = payload["after"]
        branch_ref = payload["ref"][len("refs/heads/") :]
    elif event_type == "pull_request":
        head_sha = payload["pull_request"]["head"]["sha"]
        branch_ref = payload["pull_request"]["base"]["ref"]
    elif event_type == "check_run":
        head_sha = payload["check_run"]["head_sha"]
        prs = payload["check_run"]["pull_requests"]
        branch_refs = [pr["base"]["ref"] for pr in prs]
        branch_ref = branch_refs[0] if branch_refs else "unknown"
    else:
        raise ValueError(f"Unsupported event type: {event_type}")
    repo_owner = payload["repository"]["owner"]["login"]
    repo_name = payload["repository"]["name"]
    return repo_owner, repo_name, head_sha, branch_ref


def exceeded_timeout(start_time: str | datetime | float, timeout_seconds: float = 10) -> bool:
    """Return True if elapsed time since start_time exceeds timeout_seconds.

    Accepts start_time as ISO string (with optional trailing 'Z'), datetime, or timestamp.

    Args:
        start_time: Start time reference (str, datetime, or timestamp).
        timeout_seconds: How many seconds may elapse before it is considered timed out.

    Returns:
        True if elapsed time is greater than timeout_seconds, otherwise False.

    Raises:
        ValueError: If string time cannot be parsed.
        TypeError: If start_time is of unsupported type.

    Examples:
        >>> time_now = datetime.utcnow().isoformat() + "Z"
        >>> exceeded_timeout(time_now)
        False
        >>> import time
        >>> time.sleep(1)
        >>> exceeded_timeout(time_now, timeout_seconds=1)
        True
    """
    if isinstance(start_time, str):
        ts_str = start_time.rstrip("Z")
        try:
            dt = datetime.fromisoformat(ts_str)
            # If the string had a Z, treat it as UTC
            if start_time.endswith("Z"):
                dt = dt.replace(tzinfo=timezone.utc)
        except ValueError:
            raise ValueError(f"Unrecognized time format: {start_time!r}")
        start_timestamp = dt.timestamp()
    elif isinstance(start_time, datetime):
        start_timestamp = start_time.timestamp()
    elif isinstance(start_time, int | float):
        start_timestamp = float(start_time)
    else:
        raise TypeError(f"start_time must be str, datetime, or float, got {type(start_time).__name__}")
    elapsed_time = time.time() - start_timestamp
    return elapsed_time > timeout_seconds


def setup_logging(log_filename: str) -> None:
    """Configure logging with both console and file handlers.

    Console handler shows WARNING and ERROR level messages only.
    File handler captures all log levels including DEBUG.

    Args:
        log_filename: Name of the log file to write to
    """
    logger = logging.getLogger()
    logger.setLevel(logging.DEBUG)

    # Clear any existing handlers to avoid duplicates
    logger.handlers.clear()

    # Create formatter
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")

    # Console handler (WARNING and ERROR only)
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)

    # File handler (all levels including DEBUG)
    file_handler = logging.FileHandler(log_filename)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(formatter)

    # Add handlers to logger
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
