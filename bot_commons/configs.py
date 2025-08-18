import glob
import itertools
import logging
from collections.abc import Generator
from copy import deepcopy
from pathlib import Path

import yaml

from bot_commons.utils import sanitize_params_for_env, to_bool


class ConfigWorkflow:
    """Configuration for a run, including matrix generation."""

    _RESTRICTED_PARAMETERS = ("env", "run")
    _data: dict

    def __init__(self, config: dict):
        self._data = config

    @property
    def name(self) -> str:
        """Get the name of the configuration."""
        return self._data.get("name", "Lit Job")

    @property
    def trigger(self) -> dict | list | None:
        """Get the trigger for the configuration."""
        return self._data.get("trigger", [])

    def is_triggered_by_event(self, event: str, branch: str) -> bool:
        """Check if the event is triggered by a code change."""
        return self._is_triggered_by_event(event, branch, self._data.get("trigger"))

    def append_repo_details(self, repo_owner: str, repo_name: str, head_sha: str, branch_ref: str) -> None:
        """Append repository details to the configuration."""
        self._data.update({
            "repository_owner": repo_owner,
            "repository_name": repo_name,
            "repository_ref": head_sha,
            "branch_ref": branch_ref,
        })

    @staticmethod
    def _is_triggered_by_event(event: str, branch: str, trigger: dict | list | None = None) -> bool:
        """Check if the event is triggered by a code change.

        >>> ConfigWorkflow._is_triggered_by_event("push", "main", {"push": {"branches": ["main"]}})
        True
        >>> ConfigWorkflow._is_triggered_by_event("pull_request", "main", {"pull_request": {"branches": ["main"]}})
        True
        >>> ConfigWorkflow._is_triggered_by_event("push", "feature", {"push": {"branches": ["main"]}})
        False
        >>> ConfigWorkflow._is_triggered_by_event("pull_request", "feature", {"pull_request": {"branches": ["main"]}})
        False
        >>> ConfigWorkflow._is_triggered_by_event("push", "main")
        True
        >>> ConfigWorkflow._is_triggered_by_event("pull_request", "main")
        True
        >>> ConfigWorkflow._is_triggered_by_event("issue_comment", "main", ["push"])
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

    @staticmethod
    def _generate_matrix(parametrize: dict) -> list:
        """Generate a list of parameter dictionaries from YAML config matrix.

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
        []
        """
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

    def generate_runs(self) -> Generator["ConfigRun"]:
        """Generate a list of ConfigRun objects from the configuration."""
        for params in self._generate_matrix(self._data):
            yield ConfigRun(config=self, params=params)


class ConfigRun:
    """Configuration for a run, including matrix generation."""

    _data: dict
    params: dict

    def __init__(self, config: ConfigWorkflow, params: dict):
        self._data = deepcopy(config._data)
        self.params = {k: v for k, v in params if k not in ConfigWorkflow._RESTRICTED_PARAMETERS}

    @property
    def name(self) -> str:
        """Get the name of the configuration."""
        return self.params.get("name") or self._data.get("name", "Lit Job")

    @property
    def run(self) -> str:
        """Get the run command."""
        return self._data.get("run", "")

    @property
    def env(self) -> dict:
        """Get the environment variables."""
        envs = self._data.get("env", {})
        envs.update(sanitize_params_for_env(self.params))
        return envs

    @property
    def image(self) -> str:
        """Get the machine type."""
        return self.params.get("image") or self._data.get("image", "ubuntu:22.04")

    @property
    def machine(self) -> str:
        """Get the machine type."""
        return self.params.get("machine") or self._data.get("machine", "CPU")

    @property
    def interruptible(self) -> bool:
        """Get the interruptible flag."""
        return to_bool(self.params.get("interruptible") or self._data.get("interruptible", False))

    @property
    def timeout_minutes(self) -> float:
        """Get the timeout flag."""
        return float(self.params.get("timeout") or self._data.get("timeout", 60))

    @property
    def repository_owner(self) -> str:
        """Get the repository owner."""
        return self._data.get("repository_owner")

    @property
    def repository_name(self) -> str:
        """Get the repository name."""
        return self._data.get("repository_name")

    @property
    def repository_ref(self) -> str:
        """Get the repository ref."""
        return self._data.get("repository_ref")

    @property
    def mode(self) -> str:
        """The mode can be 'info' or 'debug'."""
        return self._data.get("mode", "info")


class ConfigFile:
    """Configuration file representation."""

    path: Path
    name: str
    body: dict

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.name = self.path.name
        try:  # todo: add specific exception and yaml validation
            content = self.path.read_text(encoding="utf_8")
            config = yaml.safe_load(content)
        except yaml.YAMLError as e:
            raise RuntimeError(f"YAML parsing error in config file: {e!s}")
        except OSError as e:
            raise RuntimeError(f"File error while reading config: {e!s}")
        if not isinstance(config, dict):
            raise ValueError(f"Invalid config file format: {self.path}")
        if not config:
            raise ValueError(f"Empty config file: {self.path}")
        self.body = config

    @staticmethod
    def load_from_folder(path_dir: str | Path = ".lightning/workflows") -> list["ConfigFile"]:
        """List all configuration files in the given path."""
        path_dir = Path(path_dir).resolve()
        if not path_dir.is_dir():
            raise ValueError(f"Directory '{path_dir}' with expected action config does not exists.")

        ls_files = glob.glob(str(path_dir / "*.yaml")) + glob.glob(str(path_dir / "*.yml"))
        if not ls_files:
            return []
        return [ConfigFile(cfg_path) for cfg_path in sorted(ls_files)]
