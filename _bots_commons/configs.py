import glob
import itertools
import logging
from abc import ABC
from collections.abc import Generator
from copy import deepcopy
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

from _bots_commons.utils import sanitize_params_for_env, to_bool


class GitHubRunStatus(Enum):
    """Enum for GitHub check run statuses."""

    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class GitHubRunConclusion(Enum):
    """Enum for GitHub check run conclusions."""

    SUCCESS = "success"
    FAILURE = "failure"
    SKIPPED = "skipped"
    NEUTRAL = "neutral"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"
    ACTION_REQUIRED = "action_required"


class ConfigBase(ABC):
    def to_dict(self) -> dict[str, Any]:
        """Convert to a JSON-serializable dictionary using only attributes.

        Returns:
            A dictionary containing only instance attributes with JSON-serializable values.
        """
        result = {}

        # Get only instance attributes, not properties or methods
        for attr_name in vars(self):
            value = getattr(self, attr_name)
            # Only include JSON-serializable types
            if isinstance(value, str | int | float | bool | list | dict | type(None)):
                result[attr_name] = value

        return result


class ConfigWorkflow(ConfigBase):
    """Configuration for a run, including matrix generation.

    Examples:
        >>> dir_examples = Path(__file__).resolve().parent.parent / "examples"
        >>> dir_examples.exists()
        True
        >>> for cfg_file in ConfigFile.load_from_folder(dir_examples):
        ...     cfg = ConfigWorkflow(cfg_file.body, file_name=cfg_file.name)
        ...     print(cfg.file_name, len(list(cfg.generate_runs())))
        multi-workflow.yml 5
        simple-workflow.yml 1
    """

    _RESTRICTED_PARAMETERS = ("env", "run")
    config_body: dict
    file_name: str

    def __init__(self, config_body: dict, file_name: str = "") -> None:
        """Initialize the workflow configuration.

        Args:
            config_body: Parsed YAML configuration dictionary.
            file_name: Optional originating file name for diagnostics.
        """
        self.config_body = config_body
        self.file_name = file_name

    @property
    def name(self) -> str:
        """Get the human-readable name of the workflow."""
        return self.config_body.get("name", "Lit Job")

    @property
    def trigger(self) -> dict | list | None:
        """Return the trigger definition for this workflow."""
        return self.config_body.get("trigger", [])

    def is_triggered_by_event(self, event: str, branch: str) -> bool:
        """Return True if this workflow triggers on the given event/branch.

        Args:
            event: GitHub event name, e.g., "push" or "pull_request".
            branch: Branch name involved in the event.

        Returns:
            True if the trigger matches; otherwise False.
        """
        return self._is_triggered_by_event(event, branch, self.config_body.get("trigger"))

    def append_repo_details(self, repo_owner: str, repo_name: str, head_sha: str, branch_ref: str) -> None:
        """Append repository details to the configuration.

        Args:
            repo_owner: Repository owner login.
            repo_name: Repository name.
            head_sha: Commit SHA used to run.
            branch_ref: Branch name (ref) associated with the event.
        """
        self.config_body.update({
            "repository_owner": repo_owner,
            "repository_name": repo_name,
            "repository_ref": head_sha,
            "branch_ref": branch_ref,
        })

    @staticmethod
    def _is_triggered_by_event(event: str, branch: str, trigger: dict | list | None = None) -> bool:
        """Check if the event is triggered by a code change.

        Args:
            event: GitHub event name.
            branch: Branch name involved in the event.
            trigger: Trigger definition (dict/list) or None.

        Returns:
            True if the workflow should trigger for this event/branch; otherwise False.

        Examples:
            >>> ConfigWorkflow._is_triggered_by_event("push", "main", {"push": {"branches": ["main"]}})
            True
            >>> ConfigWorkflow._is_triggered_by_event("pull_request", "main", {"pull_request": {"branches": ["main"]}})
            True
            >>> ConfigWorkflow._is_triggered_by_event("push", "feature", {"push": {"branches": ["main"]}})
            False
            >>> ConfigWorkflow._is_triggered_by_event(
            ...     "pull_request", "feature", {"pull_request": {"branches": ["main"]}})
            False
            >>> ConfigWorkflow._is_triggered_by_event("push", "main")
            True
            >>> ConfigWorkflow._is_triggered_by_event("pull_request", "main")
            True
            >>> ConfigWorkflow._is_triggered_by_event("issue_comment", "main", ["push"])
            False
            >>> ConfigWorkflow._is_triggered_by_event("check_run", "main", ["push", "pull_request"])
            True
        """
        if not trigger:
            return True  # No specific trigger, assume all events are valid
        if event == "check_run":
            # "check_run" events are always considered triggered because they are generated by GitHub Actions
            # after a workflow has already started. They are not user-initiated and do not depend on the trigger
            # configuration. Thus, we always return True for "check_run" events.
            return True
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

    @staticmethod
    def _generate_matrix(parametrize: dict[str, Any]) -> list[dict[str, Any]]:
        """Generate a list of parameter dictionaries from YAML config matrix.

        Args:
            parametrize: A dict potentially containing "matrix", "include", and "exclude" keys.

        Returns:
            A list of dictionaries, each representing a single parameter combination.

        Examples:
            >>> config_multi = {
            ...     "matrix": {
            ...         "image": ["ubuntu", "windows"],
            ...         "python": ["3.10", "3.11"]
            ...     },
            ...     "include": [{"python": "3.9", "image": "ubuntu"}],
            ...     "exclude": [{"python": "3.10", "image": "windows"}]
            ... }
            >>> params = ConfigWorkflow._generate_matrix(config_multi)
            >>> len(params)
            4
            >>> {"python": "3.11", "image": "windows"} in params
            True
            >>> {"python": "3.9", "image": "ubuntu"} in params
            True
            >>> {"python": "3.10", "image": "windows"} in params
            False

            >>> empty_config = {}
            >>> ConfigWorkflow._generate_matrix(empty_config)
            [{}]
        """
        if not parametrize:
            # edge case: if the config is empty, return a list with an empty configuration
            # this makes it run just once with no parameters
            return [{}]
        matrix = parametrize.get("matrix", {})
        include = parametrize.get("include", [])
        exclude = parametrize.get("exclude", [])
        all_combinations = []

        # Start with base matrix combinations
        if matrix:
            # Get all matrix keys and their values
            keys = list(matrix.keys())
            if any(key in ConfigWorkflow._RESTRICTED_PARAMETERS for key in keys):
                raise ValueError(
                    f"Parameters {', '.join(ConfigWorkflow._RESTRICTED_PARAMETERS)} are not allowed in the matrix."
                )
            values = list(matrix.values())

            # Generate all combinations
            for combination in itertools.product(*values):
                all_combinations.append(dict(zip(keys, combination)))

        # Add include items
        for item in include:
            if not isinstance(item, dict):
                raise ValueError("Include items must be dictionaries.")
            if any(k in ConfigWorkflow._RESTRICTED_PARAMETERS for k in item):
                raise ValueError(
                    f"Parameters {', '.join(ConfigWorkflow._RESTRICTED_PARAMETERS)}"
                    " are not allowed in the include items."
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

    @property
    def parametrize(self) -> dict[str, Any]:
        """Return the 'parametrize' section from the configuration."""
        return self.config_body.get("parametrize", {})

    def generate_runs(self) -> Generator["ConfigRun"]:
        """Generate a list of ConfigRun objects from the configuration.

        Yields:
            ConfigRun: One run configuration per parameter combination.
        """
        for params in self._generate_matrix(self.parametrize):
            yield ConfigRun(config_body=self.config_body, params=params, file_name=self.file_name)


class ConfigRun(ConfigBase):
    """Configuration for a run, including matrix generation.

    Examples:
        >>> dir_examples = Path(__file__).resolve().parent.parent / "examples"
        >>> dir_examples.exists()
        True
        >>> cfg_files = ConfigFile.load_from_folder(dir_examples)
        >>> cfg = ConfigWorkflow(cfg_files[0].body)
        >>> runs = list(cfg.generate_runs())
        >>> runs[0].name
        'PR Validation Workflow'
        >>> runs[0].image
        'python:3.10-slim-bookworm'
        >>> from pprint import pprint
        >>> pprint(runs[0].env)
        {'HELLO': 'world',
         'TEST_ENV': 'ci',
         'image': 'python:3.10-slim-bookworm',
         'machine': 'CPU'}
    """

    config_body: dict
    params: dict
    file_name: str

    def __init__(self, config_body: dict, params: dict, file_name: str) -> None:
        """Initialize a ConfigRun.

        Args:
            config_body: The base workflow configuration dictionary.
            params: Parameter combination produced by matrix expansion.
            file_name: Source file name for this configuration.
        """
        self.config_body = deepcopy(config_body)
        self.params = {k: v for k, v in params.items() if k not in ConfigWorkflow._RESTRICTED_PARAMETERS}
        self.file_name = file_name

    @property
    def name(self) -> str:
        """Return the run name."""
        return self.params.get("name") or self.config_body.get("name", "Lit Job")

    @property
    def check_name(self) -> str:
        run_params = [p or "n/a" for p in self.params.values()]
        return f"{self.file_name} / {self.name} ({', '.join(run_params)})"

    @property
    def run(self) -> str:
        """Return the shell command(s) to execute inside the container."""
        return self.config_body.get("run", "")

    @property
    def env(self) -> dict[str, Any]:
        """Return environment variables for the run."""
        envs = deepcopy(self.config_body.get("env", {}))
        envs.update(sanitize_params_for_env(self.params))
        # extra envs about the environment
        envs["image"] = self.image
        envs["machine"] = self.machine
        return envs

    @property
    def image(self) -> str:
        """Return the container image reference (e.g., 'ubuntu:22.04')."""
        return self.params.get("image") or self.config_body.get("image", "ubuntu:22.04")

    @property
    def machine(self) -> str:
        """Return the machine type (e.g., 'CPU', 'GPU')."""
        return self.params.get("machine") or self.config_body.get("machine", "CPU")

    @property
    def interruptible(self) -> bool:
        """Return whether the job may be preempted (interruptible)."""
        return to_bool(self.params.get("interruptible") or self.config_body.get("interruptible", False))

    @property
    def timeout_minutes(self) -> float:
        """Return the execution timeout in minutes."""
        return float(self.params.get("timeout") or self.config_body.get("timeout", 60))

    @property
    def repository_owner(self) -> str:
        """Return the repository owner login."""
        return self.config_body.get("repository_owner")

    @property
    def repository_name(self) -> str:
        """Return the repository name."""
        return self.config_body.get("repository_name")

    @property
    def repository_ref(self) -> str:
        """Return the commit SHA reference for the run."""
        return self.config_body.get("repository_ref")

    @property
    def mode(self) -> str:
        """Return the mode string ('info' or 'debug')."""
        return self.config_body.get("mode", "info")


class ConfigFile(ConfigBase):
    """Configuration file representation."""

    path: Path
    name: str
    body: dict

    def __init__(self, path: str | Path) -> None:
        """Load and parse a YAML workflow configuration file.

        Args:
            path: Path to the YAML configuration file.

        Raises:
            RuntimeError: If the file cannot be read or parsed as YAML.
            ValueError: If the parsed content is not a non-empty mapping.
        """
        self.path = Path(path)
        self.name = self.path.name
        try:  # todo: add specific exception and yaml validation
            content = self.path.read_text(encoding="utf_8")
            config = yaml.safe_load(content)
        except yaml.YAMLError as err:
            raise RuntimeError(f"YAML parsing error in config file: {err!s}")
        except OSError as err:
            raise RuntimeError(f"File error while reading config: {err!s}")
        if not isinstance(config, dict):
            raise ValueError(f"Invalid config file format: {self.path}")
        if not config:
            raise ValueError(f"Empty config file: {self.path}")
        self.body = config

    @classmethod
    def load_from_folder(cls, path_dir: str | Path = ".lightning/workflows") -> list["ConfigFile"]:
        """List all configuration files in the given directory.

        Args:
            path_dir: Directory that contains workflow YAML files.

        Returns:
            A list of ConfigFile instances sorted by file name.

        Raises:
            ValueError: If the directory does not exist.

        Examples:
            >>> dir_examples = Path(__file__).resolve().parent.parent / "examples"
            >>> dir_examples.exists()
            True
            >>> configs = ConfigFile.load_from_folder(dir_examples)
            >>> len(configs)
            2
            >>> [cfg.name for cfg in configs]
            ['multi-workflow.yml', 'simple-workflow.yml']
        """
        path_dir = Path(path_dir).resolve()
        if not path_dir.is_dir():
            raise ValueError(f"Directory '{path_dir}' with expected action config does not exists.")

        ls_files = glob.glob(str(path_dir / "*.yaml")) + glob.glob(str(path_dir / "*.yml"))
        if not ls_files:
            return []
        return [cls(cfg_path) for cfg_path in sorted(ls_files)]
