# Bot Commons

This package contains common code used across our bots.

## Modules

- **configs.py**: This module contains configurations for the bots.
- **lit_job.py**: This module contains the definition of a job.
- **utils.py**: This module contains utility functions.

## Workflow Configuration

The bot reads YAML workflow configuration files from the `.lightning/workflows/` directory in your repository.
See the [sample configuration file](../examples/simple-workflow.yml) for an example.

### Multiple Configurations

You can have multiple workflow configuration files in the `.lightning/workflows/` directory for different validation scenarios:

- `.lightning/workflows/pr-checks.yml` - Main PR validation workflow
- `.lightning/workflows/docker-compile.yml` - Docker image compilation checks
- _etc._

Each configuration file is processed independently, allowing for modular and organized validation workflows.

## Some Configuration Options

### Parametrize Matrix

- **matrix** - Define multiple combinations of environments to test
- **include** - Add specific parameter combinations
- **exclude** - Remove specific parameter combinations from the matrix

### Environment Variables

- **env** - Set environment variables for the workflow execution
- all parameters from parametrization are available as environment variables during execution

### Execution

- **image** - Docker image to use for running the workflow (can be overridden by the matrix)
- **run** - Shell commands to execute for validation
