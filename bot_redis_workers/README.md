# GitHub App Bot with Redis Queue and Workers

This repository contains a refactored GitHub App bot that integrates with GitHub webhooks to handle repository events like push and pull requests.
It uses a Redis queue for task management, making the bot restart-resistant and scalable with a pool of workers.
The bot processes tasks in phases: handling new events, starting jobs (using Lightning Jobs), waiting for job completion, and processing results to report back to GitHub.
Workflow execution is controlled by configurable trigger criteria that determine which events and branches should initiate validation.

The code is written in Python and uses `gidgethub[aiohttp]` for webhook handling, `redis-py` for queuing, and other minimal dependencies.

## Features

- Asynchronous webhook handling for GitHub events (e.g., push, pull_request synchronized).
- YAML-based trigger configuration allows filtering by event type (push/pull_request) and branch patterns.
- Support for `check_run` rerequested events, allowing users to retry failed validations by clicking "Re-run" on GitHub check runs.
- Redis-based queue for tasks, allowing multiple workers to process in parallel.
- Phased task lifecycle: New event → Start job → Wait job → Process results.
- Restart resistance: Unprocessed tasks remain in the queue if workers crash.
- Minimal and organized code structure.

## Setup - Create a GitHub App

- Go to [GitHub Apps](https://github.com/settings/apps) and create a new app.
- Set webhook URL to your server's endpoint (e.g., `https://your-domain.com/`).
- Note down: App ID, Private Key (PEM file), Webhook Secret.
- Install the app on target repositories and subscribe to "Push", "Pull requests", and "Check runs" events.

## How to Start

### Using Docker Compose

Quickly bring up Redis, the webhook app, and workers with one command.

1. **Prerequisites:**

   - Install Docker and Docker Compose on your machine.
   - Ensure you have your GitHub App credentials: GH_APP_ID, GH_PRIVATE_KEY (PEM content), GH_WEBHOOK_SECRET.

2. **Set environment variables:**

   - __Option A (recommended)__: create a `.env` file in the project root with:
     - `GH_APP_ID=...`
     - `GH_PRIVATE_KEY="..."`
     - `GH_WEBHOOK_SECRET=...`
   - Option B: export them in your shell before running Compose.

3. **Start services:**

   - Run: `docker-compose up -d`
   - This starts:
     - Redis (default port 6379)
     - Webhook server (port 8080)
     - One or more workers

4. **Check logs:**

   - All services: `docker-compose logs -f`
   - App only: `docker-compose logs -f app`
   - Worker only: `docker-compose logs -f worker`

5. **Scale workers** - Increase to 5 workers: `docker-compose up -d --scale worker=5`

6. **Stop and clean up** - `docker-compose down`

7. **Point your GitHub App webhook:**

   - Use your server’s public URL on port 8080 (e.g., https://your-host:8080/).
   - For local development, expose 8080 publicly using a tunneling tool and set the webhook URL accordingly.

### Manual Setup

Install the **Python dependencies** listed in `requirements.txt`

**Redis Server**:
This is required for the queue system. Redis is not a Python package, so install it separately on your system:

- On macOS: `brew install redis`
- On Ubuntu/Debian: `sudo apt update && sudo apt install redis-server`
- On Windows: Use WSL or download from the official Redis website.
- Verify installation: Run `redis-server --version`

**Environment Variables**:
Set these in your shell or a `.env` file (load with `dotenv` if needed, but not included here for minimalism):

- `GH_APP_ID`: Your GitHub App ID.
- `GH_PRIVATE_KEY`: The content of your private key PEM file (use `$(cat path/to/private-key.pem)`).
- `GH_WEBHOOK_SECRET`: Your webhook secret.
- `REDIS_URL`: Optional; defaults to `redis://localhost:6379/0`. Change if using a remote Redis.

## Task Lifecycle

The bot processes GitHub events through a queued, phased lifecycle to ensure reliability and scalability:

1. **Webhook Reception (New Event)**:

   - GitHub sends a webhook event (e.g., push to branch, pull_request opened/synchronized, or check_run rerequested) to the server (`app.py`).
   - For `check_run` events with `rerequested` action, the bot identifies which specific check failed and needs to be rerun.
   - The event is validated and enqueued as a task of type `'new_event'` with the payload (repo details, commit SHA, branch info, etc.) into the Redis queue (`bot_queue`).

2. **New Event Processing**:

   - A worker pops the `'new_event'` task.
   - Downloads the repository's `.lightning/workflows` folder.
   - Parses YAML configuration files and applies trigger filtering (event type, branch patterns).
   - Generates run configurations only for workflows that match the current event and branch.
   - Enqueues a separate `'start_job'` task for each applicable configuration.

3. **Start Job**:

   - Worker pops `'start_job'`.
   - Starts a Lightning Job (litJob) with the config (placeholder: returns a job_id).
   - Enqueues a `'wait_job'` task with the job_id and status `'running'`.

4. **Wait Job**:

   - Worker pops `'wait_job'`.
   - Checks the job status (placeholder: via Lightning SDK).
   - If still running: Re-enqueues the same `'wait_job'` task.
   - If finished: Enqueues a `'results'` task.
   - (Handle failures by adding error status or notifications.)

5. **Process Results**:

   - Worker pops `'results'`.
   - Fetches job logs (placeholder).
   - Summarizes results.
   - Posts the summary as a comment on the GitHub PR.
   - Task completes; no further enqueue.

This phased approach allows workers to handle tasks concurrently. If a worker restarts, pending tasks remain in the queue. Use multiple workers for load balancing.

### Consolidated Job Processing Flow:

```
┌─────────────────┐
│ GitHub Event    │ (e.g., push, pull_request, workflow_dispatch)
│   (Webhook)     │
└─────────┬───────┘
          │
          ▼
┌─────────────────┐
│ NEW_EVENT Phase │ - Download repo .lightning/workflows folder
│ (Event Process) │ - Parse configuration files
│                 │ - Check event triggers & generate job runs
└─────────┬───────┘
          │
          ▼
┌──────────────────────────────────────────────────────┐
│              Multiple Job Runs Generated             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │
│  │ Job Run 1.1 │  │ Job Run 2.1 │  │ Job Run N.M │   │
│  │ Job Run 1.2 │  │ Job Run 2.2 │  │     ...     │   │
│  │     ...     │  │     ...     │  │             │   │
│  └─────────────┘  └─────────────┘  └─────────────┘   │
└─────────────────┬────────────────────────────────────┘
                  │ (Each job follows same path)
                  ▼
        ┌─────────────────┐
        │ START_JOB Phase │ - Create GitHub check run
        │  (Launch Jobs)  │ - Launch Lightning AI job
        └─────────┬───────┘
                  │
                  ▼
        ┌──────────────────┐
        │  WAIT_JOB Phase  │ ◀─── Re-queue if still running/pending
        │ (Monitor Status) │
        └─────────┬────────┘
                  │ (Decision Point)
                  ▼
            ┌─────────────┐
            │ Job Status  │
            │ Evaluation  │
            └──────┬──────┘
                   │
     ┌─────────────┼─────────────────┐
     │             │                 │
     ▼             ▼                 ▼
┌─────────┐   ┌──────────┐    ┌──────────────┐
│ TIMEOUT │   │ FINISHED │    │ STILL ACTIVE │
└────┬────┘   └────┬─────┘    └──────┬───────┘
     │             │                 │
     ▼             ▼                 ▼
┌─────────┐    ┌─────────┐        ┌──────┐
│ FAILURE │    │ RESULTS │        │ WAIT │
│  Phase  │    │  Phase  │        │(Loop)│
└─────────┘    └─────────┘        └──────┘
     │              │
     ▼              ▼
┌───────────┐   ┌───────────────┐
│ GH Status:│   │    Process    │
│ CANCELLED │   │ Logs & Report │
└───────────┘   └────┬──────────┘
                     │
                     ▼
             ┌───────────────┐
             │ GitHub Status:│
             │  - SUCCESS    │
             │  - FAILURE    │
             │  - NEUTRAL    │
             └───────────────┘
```

### Legend:

```
┌─────────┐
│ PHASE   │    Phases (Redis queue tasks)
└─────────┘

┌──────────┐
│ Decision │    Decision points
└──────────┘

◀───           Re-queue loop
```

1. **Install Dependencies**:

   ```bash
   # Install Python dependencies
   pip install -r requirements.txt

   # Install Redis server
   # macOS: brew install redis
   # Ubuntu: sudo apt install redis-server
   # Windows: Use WSL or Redis for Windows
   ```

2. **Set Environment Variables**:

   ```bash
   export GH_APP_ID="123456"
   export GH_PRIVATE_KEY="$(cat path/to/private-key.pem)"
   export GH_WEBHOOK_SECRET="your_webhook_secret"
   export REDIS_URL="redis://localhost:6379/0"  # optional
   ```

3. **Start Services**:

   ```bash
   # Terminal 1: Start Redis
   redis-server

   # Terminal 2: Start webhook server
   cd bot_redis_workers
   python app.py

   # Terminal 3: Start worker
   cd bot_redis_workers
   python worker.py

   # Terminal 4: Start additional workers (optional)
   cd bot_redis_workers
   python worker.py
   ```

4. **Verify Setup**:

   ```bash
   # Check Redis connection
   redis-cli ping  # Should return "PONG"

   # Check webhook server
   curl -X POST http://localhost:8080/

   # Monitor logs in terminals running app.py and worker.py
   ```

## Testing and Usage

1. **Create or Update a PR, or Push Code**:

   - Go to a repository where your GitHub App is installed
   - Push commits to a branch or create/update a pull request to trigger webhook events
   - The bot will process events based on your workflow trigger configuration

2. **Monitor Activity**:

   ```bash
   # Check Redis queue
   redis-cli LLEN bot_queue      # Queue length
   redis-cli LRANGE bot_queue 0 -1  # View all tasks

   # Clear queue if needed
   redis-cli DEL bot_queue
   ```

3. **Expected Behavior**:

   - Webhook server logs: "Enqueued new_event for push...", "Enqueued new_event for PR...", or "Enqueued new_event for check_run..."
   - Worker logs: "Successfully processed task..." with trigger filtering details
   - GitHub: Check runs appear on commits/PRs with status updates based on configured triggers
   - For rerun events: Only the specific failed check that was requested to rerun will be processed

## Code Structure

- `app.py`: Webhook server using `gidgethub[aiohttp]`.
- `worker.py`: Worker loop to process queued tasks.
- `tasks.py`: Logic for each task type (e.g., start_lit_job placeholders).

## Troubleshooting

- **Webhook Issues**: Ensure your server is publicly accessible and the webhook secret matches.
- **Redis Connection**: If using remote Redis, update `REDIS_URL` and check firewall/port 6379.
- **GitHub API Limits**: Monitor rate limits; use app authentication properly.
- **Customizations**: Replace placeholders in `tasks.py` with your litJob code. Add error handling/logging as needed.
