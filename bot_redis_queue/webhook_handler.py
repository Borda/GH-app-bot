"""GitHub webhook handler that queues events to Redis."""

import asyncio
import logging
import time
from typing import Dict, Any

import aiohttp
import jwt  # PyJWT
from aiohttp import web
from gidgethub import sansio
from gidgethub.aiohttp import GitHubAPI
from gidgethub.apps import get_installation_access_token

from .tasks import process_github_event

logger = logging.getLogger(__name__)


async def handle_github_webhook(
    request: web.Request, 
    github_app_id: str, 
    private_key: str, 
    webhook_secret: str = ""
) -> web.Response:
    """
    Handle incoming GitHub webhook events by queuing them to Redis.

    This handler:
    1. Validates the webhook signature
    2. Parses the GitHub event
    3. Queues the event for background processing
    4. Returns immediate acknowledgment
    """
    try:
        # Read the raw request body
        body = await request.read()
    except ConnectionResetError:
        logger.warning("Client disconnected before request body was fully read")
        return web.Response(status=400, text="Connection reset")
    except Exception as exc:
        logger.error("Failed to read request body", exc_info=exc)
        return web.Response(status=400, text="Failed to read request body")

    try:
        # Parse and validate the GitHub webhook event
        event = sansio.Event.from_http(request.headers, body, secret=webhook_secret)
    except Exception as exc:
        logger.error("Failed to parse webhook event", exc_info=exc)
        return web.Response(status=400, text="Invalid webhook event")

    # Extract relevant event data
    event_data = {
        "event_type": event.event_type,
        "action": event.data.get("action"),
        "installation": event.data.get("installation"),
        "repository": event.data.get("repository"),
        "pull_request": event.data.get("pull_request"),
        "push": {
            "after": event.data.get("after"),
            "before": event.data.get("before"),
            "commits": event.data.get("commits"),
        } if event.event_type == "push" else None,
        "raw_data": event.data,  # Keep full data for complex processing
        "headers": dict(request.headers),
        "github_app_id": github_app_id,
        "private_key": private_key,
    }

    try:
        # Queue the event for background processing
        task_result = process_github_event.delay(event_data)
        logger.info(
            f"Queued GitHub event: {event.event_type} "
            f"(action: {event.data.get('action', 'N/A')}) "
            f"with task ID: {task_result.id}"
        )

        return web.Response(
            status=202, 
            text=f"Event queued with task ID: {task_result.id}"
        )

    except Exception as exc:
        logger.error("Failed to queue event for processing", exc_info=exc)
        return web.Response(status=500, text="Failed to queue event")


async def authenticate_github_app(
    github_app_id: str, 
    private_key: str, 
    installation_id: int
) -> str:
    """
    Authenticate with GitHub App and get installation access token.

    This function is used by workers that need to interact with GitHub API.
    """
    # Generate JWT for app authentication
    jwt_token = jwt.encode(
        {
            "iat": int(time.time()) - 60,
            "exp": int(time.time()) + (10 * 60),
            "iss": github_app_id
        },
        private_key,
        algorithm="RS256",
    )

    async with aiohttp.ClientSession() as session:
        # Exchange JWT for installation token
        app_gh = GitHubAPI(session, "github-bot-redis", oauth_token=jwt_token)
        token_resp = await get_installation_access_token(
            app_gh, 
            installation_id=installation_id, 
            app_id=int(github_app_id), 
            private_key=private_key
        )
        return token_resp["token"]


def create_webhook_handler(github_app_id: str, private_key: str, webhook_secret: str = ""):
    """Create a webhook handler with the given configuration."""
    async def handler(request: web.Request) -> web.Response:
        return await handle_github_webhook(request, github_app_id, private_key, webhook_secret)

    return handler
