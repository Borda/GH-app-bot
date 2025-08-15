# GitHub Actions Bot with Redis Queue

This bot receives GitHub webhook events and processes them using a Redis queue with Celery workers. It's designed to handle GitHub events asynchronously and perform actions like generating jobs from repository configurations, monitoring their completion, and posting status updates.

## Architecture

- **Webhook Handler**: Receives GitHub events and queues them to Redis.
- **Celery Workers**: Process tasks from the queue in the background.
- **Redis**: Acts as the message broker and result backend for Celery.
- **Lightning SDK Integration**: The bot is designed to create and monitor CI/CD jobs using the Lightning platform.

### Job Lifecycle

The lifecycle of a job, from a GitHub event to a final status update, follows this flow:


