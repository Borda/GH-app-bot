# Python GitHub Bot

A GitHub App/bot for automated PR checks and validation using configurable YAML workflows.

## Use Case

This bot automatically performs checks on pull requests based on YAML workflow configuration files.
It validates code quality, runs tests across different environments, enforces project standards, and ensures PR compliance before merging.

## Setting Up the Bot

1. Run the bot locally or deploy it to a server
2. Create and Install the GitHub App on your repository
3. The bot will automatically start monitoring pull requests and execute the defined workflows

## Configuration

The bot reads YAML workflow configuration files from the `.lightning/workflows/` directory in your repository.

### Expected YAML Structure

```yaml
# .lightning/workflows/pr-checks.yml
name: "PR Validation Workflow"  # Name of the workflow
image: "python:3.11-slim-bookworm"
machine: "CPU"  # can be overridden by the matrix
interruptible: true  # uses spot instances for cost efficiency
timeout: 60  # Timeout for each running (not queued) job in minutes
mode: "debug"  # would share full logs

env:
  HELLO: "world"
  TEST_ENV: "ci"

trigger: # Define when the workflow should run, by default, it runs on PRs and all pushes
  push:  # Trigger on pushes to the repository
    branches: ["main"]  # Trigger on pushes to these branches
  pull_request:  # Trigger on pull requests
    branches: ["main"]  # Only for PRs targeting the main branch

parametrize:
  matrix:
    image: ["python:3.10-slim-bookworm", "python:3.11-slim-bookworm"]
    machine: ["CPU", "L4", "T4"]
  include: []
  exclude:
    - {"image": "python:3.10-slim-bookworm", "machine": "L4"}


run: |
  echo "Starting now..."
  pwd
  ls -lh
  echo "Environment: $HELLO"
  pip install -r requirements.txt
  pytest -v .
  echo "Validation completed"
```

## Multiple Configurations

You can have multiple workflow configuration files in the `.lightning/workflows/` directory for different validation scenarios:

- `.lightning/workflows/pr-checks.yml` - Main PR validation workflow
- `.lightning/workflows/docker-compile.yml` - Docker image compilation checks
- _etc._

Each configuration file is processed independently, allowing for modular and organized validation workflows.

## Configuration Options

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
