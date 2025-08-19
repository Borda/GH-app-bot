# GitHub App Bot with Redis Queue and Workers

This repository contains a refactored GitHub App bot that integrates with GitHub webhooks to handle events like pull requests. It uses a Redis queue for task management, making the bot restart-resistant and scalable with a pool of workers. The bot processes tasks in phases: handling new events, starting jobs (using Lightning Jobs as placeholders), waiting for job completion, and processing results to report back to GitHub.

The code is written in Python and uses `gidgethub[aiohttp]` for webhook handling, `redis-py` for queuing, and other minimal dependencies. Placeholders are included for Lightning Job (litJob) logic—replace these with your actual implementation from the original `py_bot`.

## Features

- Asynchronous webhook handling for GitHub events (e.g., pull_request opened/synchronized).
- Redis-based queue for tasks, allowing multiple workers to process in parallel.
- Phased task lifecycle: New event → Start job → Wait job → Process results.
- Restart resistance: Unprocessed tasks remain in the queue if workers crash.
- Minimal and organized code structure.

## Requirements

### Python Packages

Install the Python dependencies listed in `requirements.txt`

### Additional Installations (Beyond requirements.txt)

- **Redis Server**: This is required for the queue system. Redis is not a Python package, so install it separately on your system:
  - On macOS: `brew install redis`
  - On Ubuntu/Debian: `sudo apt update && sudo apt install redis-server`
  - On Windows: Use WSL or download from the official Redis website.
  - Verify installation: Run `redis-server --version`
- No other external tools are needed, but ensure you have Git installed if using `gitpython` for repo cloning (usually pre-installed on most systems).

## Setup

1. **Create a GitHub App**:

   - Go to [GitHub Apps](https://github.com/settings/apps) and create a new app.
   - Set webhook URL to your server's endpoint (e.g., `https://your-domain.com/`).
   - Note down: App ID, Private Key (PEM file), Webhook Secret.
   - Install the app on target repositories and subscribe to "Pull requests" events.

2. **Environment Variables**:
   Set these in your shell or a `.env` file (load with `dotenv` if needed, but not included here for minimalism):

   - `GH_APP_ID`: Your GitHub App ID.
   - `GH_PRIVATE_KEY`: The content of your private key PEM file (use `$(cat path/to/private-key.pem)`).
   - `GH_WEBHOOK_SECRET`: Your webhook secret.
   - `REDIS_URL`: Optional; defaults to `redis://localhost:6379/0`. Change if using a remote Redis.

## Task Lifecycle

The bot processes GitHub events through a queued, phased lifecycle to ensure reliability and scalability:

1. **Webhook Reception (New Event)**:

   - GitHub sends a webhook event (e.g., pull_request opened or synchronized) to the server (`app.py`).
   - The event is validated and enqueued as a task of type `'new_event'` with the payload (repo details, PR number, etc.) into the Redis queue (`bot_queue`).

2. **New Event Processing**:

   - A worker pops the `'new_event'` task.
   - Clones/pulls the repository.
   - Generates run configurations (e.g., from a config file like `.litci.yaml`—placeholder logic).
   - Enqueues a separate `'start_job'` task for each configuration.

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

## How to Start

1. **Start Redis Server**:

   - Run `redis-server` in a terminal (or as a background service: `redis-server &`).
   - Confirm it's running: `redis-cli ping` should return "PONG".

2. **Start the Webhook Server**:

   - Run `python app.py`.
   - The server listens on port 8080

3. **Start Workers**:

   - Open one or more terminals.
   - In each, run `python worker.py`.
   - Start with 2-5 workers for a basic pool; scale based on load.
   - Workers run in an infinite loop, popping and processing tasks from the queue.

4. **Testing**:

   - Create or update a PR in a repo where the GitHub App is installed.
   - Monitor console logs in `app.py` and `worker.py` for enqueue/dequeue actions.
   - Use `redis-cli` to inspect the queue: `llen bot_queue` (length) or `lrange bot_queue 0 -1` (view tasks).
   - Simulate restarts: Kill a worker; tasks should be picked up by others.

## Code Structure

- `app.py`: Webhook server using `gidgethub[aiohttp]`.
- `worker.py`: Worker loop to process queued tasks.
- `tasks.py`: Logic for each task type (e.g., start_lit_job placeholders).

## Troubleshooting

- **Webhook Issues**: Ensure your server is publicly accessible and the webhook secret matches.
- **Redis Connection**: If using remote Redis, update `REDIS_URL` and check firewall/port 6379.
- **GitHub API Limits**: Monitor rate limits; use app authentication properly.
- **Customizations**: Replace placeholders in `tasks.py` with your litJob code. Add error handling/logging as needed.

## Contributing

Fork the repo, make changes, and submit a PR. For issues, open a GitHub issue.

Built based on the original `py_bot` from https://github.com/Borda/GitHub-app-bot.
