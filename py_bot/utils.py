import hashlib
import itertools
import os
import time

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


def generate_unique_hash(length=16):
    """Generate a unique hash string of specified length."""
    # Use current timestamp and a counter for uniqueness
    unique_string = f"{time.time()}{os.getpid()}"
    hash_object = hashlib.md5(unique_string.encode())
    return hash_object.hexdigest()[:length]
