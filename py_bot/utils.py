import glob
import hashlib
import itertools
import logging
import os
import time
import zipfile
from pathlib import Path
from textwrap import TextWrapper

import yaml

_RESTRICTED_PARAMETERS = ("env", "run")


def generate_matrix_from_config(config_parametrize: dict) -> list:
    """Generate a list of parameter dictionaries from YAML config matrix.

    >>> config_multi = {
    ...     "matrix": {
    ...         "python": ["3.10", "3.11"],
    ...         "os": ["ubuntu", "windows"]
    ...     },
    ...     "include": [{"python": "3.9", "os": "ubuntu"}],
    ...     "exclude": [{"python": "3.10", "os": "windows"}]
    ... }
    >>> result = generate_matrix_from_config(config_multi)
    >>> len(result)
    4
    >>> {"python": "3.9", "os": "ubuntu"} in result
    True
    >>> {"python": "3.10", "os": "windows"} in result
    False

    >>> empty_config = {}
    >>> generate_matrix_from_config(empty_config)
    []
    """
    matrix = config_parametrize.get("matrix", {})
    include = config_parametrize.get("include", [])
    exclude = config_parametrize.get("exclude", [])
    all_combinations = []

    # Start with base matrix combinations
    if matrix:
        # Get all matrix keys and their values
        keys = list(matrix.keys())
        if any(key in _RESTRICTED_PARAMETERS for key in keys):
            raise ValueError(f"Parameters {', '.join(_RESTRICTED_PARAMETERS)} are not allowed in the matrix.")
        values = list(matrix.values())

        # Generate all combinations
        for combination in itertools.product(*values):
            all_combinations.append(dict(zip(keys, combination)))

    # Add include items
    if include:
        if any(not isinstance(item, dict) for item in include):
            raise ValueError("Include items must be dictionaries.")
        for item in include:
            if any(k in _RESTRICTED_PARAMETERS for k in item):
                raise ValueError(
                    f"Parameters {', '.join(_RESTRICTED_PARAMETERS)} are not allowed in the include items."
                )
            all_combinations.append(item)

    # Remove exclude items
    if not exclude:
        return all_combinations

    # Filter out excluded combinations
    filtered_combinations = []
    for combo in all_combinations:
        should_exclude = False
        for exclude_item in exclude:
            # todo: make the rule behaving as GitHub Actions matrix exclude
            if all(combo.get(k) == v for k, v in exclude_item.items()):
                should_exclude = True
                break
        if not should_exclude:
            filtered_combinations.append(combo)
    return filtered_combinations


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


def load_configs_from_folder(path_dir: str | Path = ".lightning/workflows") -> list[tuple[str, dict]]:
    """List all configuration files in the given path."""
    path_dir = Path(path_dir).resolve()
    if not path_dir.is_dir():
        raise ValueError(f"Directory '{path_dir}' with expected action config does not exists.")

    ls_files = glob.glob(str(path_dir / "*.yaml")) + glob.glob(str(path_dir / "*.yml"))
    if not ls_files:
        return []

    configs = []
    for cfg_path in sorted(ls_files):
        try:  # todo: add specific exception and yaml validation
            content = Path(cfg_path).read_text(encoding="utf_8")
            config = yaml.safe_load(content)
        except yaml.YAMLError as e:
            raise RuntimeError(f"YAML parsing error in config file: {e!s}")
        except OSError as e:
            raise RuntimeError(f"File error while reading config: {e!s}")
        if not isinstance(config, dict):
            raise ValueError(f"Invalid config file format: {cfg_path}")
        if not config:
            raise ValueError(f"Empty config file: {cfg_path}")
        configs.append((Path(cfg_path).name, config))
    return configs


def is_triggered_by_event(event: str, branch: str, trigger: dict | list | None = None) -> bool:
    """Check if the event is triggered by a code change.

    >>> is_triggered_by_event("push", "main", {"push": {"branches": ["main"]}})
    True
    >>> is_triggered_by_event("pull_request", "main", {"pull_request": {"branches": ["main"]}})
    True
    >>> is_triggered_by_event("push", "feature", {"push": {"branches": ["main"]}})
    False
    >>> is_triggered_by_event("pull_request", "feature", {"pull_request": {"branches": ["main"]}})
    False
    >>> is_triggered_by_event("push", "main")
    True
    >>> is_triggered_by_event("pull_request", "main")
    True
    >>> is_triggered_by_event("issue_comment", "main", ["push"])
    False
    """
    if not trigger:
        return True  # No specific trigger, assume all events are valid
    # if isinstance(trigger, str):
    #     return trigger == event
    if isinstance(trigger, list):
        return event in trigger
    if event not in trigger:
        logging.warning(f"Event {event} is not in the trigger list: {trigger}")
        return False
    branches = trigger[event].get("branches", [])
    if branches:
        # Check if the branch is in the list of branches
        return any(ob == branch for ob in branches)  # todo: update it to reqex
    return True  # if the event is fine but no branch specified


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


def wrap_long_lines(text: str, width: int = 200) -> str:
    """Wrap long lines in the text to a specified width, using '>' as the continuation symbol.

    >>> my_text = '''Well, well, well, what do we have here?
    ... This is a very long line that should be wrapped to fit within N characters.
    ... It should also handle multiple lines correctly.
    ... '''
    >>> print(wrap_long_lines(my_text, width=56))
    Well, well, well, what do we have here?
    This is a very long line that should be wrapped to fit
    ↪ within N characters.
    It should also handle multiple lines correctly.
    """
    wrapper = TextWrapper(
        width=width,
        replace_whitespace=False,
        subsequent_indent="↪ ",  # Unicode character for right arrow with hook (↪️
    )
    lines = [wrapper.fill(line) for line in text.splitlines()]
    return "\n".join(lines)
