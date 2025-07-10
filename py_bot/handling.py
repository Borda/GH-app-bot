import asyncio
import os
import time
from pathlib import Path

import aiohttp
import jwt  # PyJWT
from aiohttp import web
from gidgethub import sansio
from gidgethub.aiohttp import GitHubAPI
from gidgethub.apps import get_installation_access_token


async def handle_webhook(request):
    print("=== webhook hit ===")
    body = await request.read()
    secret = os.getenv("WEBHOOK_SECRET", "")
    event = sansio.Event.from_http(request.headers, body, secret=secret)

    # Get router from app
    router = request.app["router"]

    # 1) App authentication → JWT
    app_id = os.getenv("GITHUB_APP_ID")
    with open(os.getenv("PRIVATE_KEY_PATH"), "rb") as fp:
        private_key = fp.read()  # Read the private key from the file
    jwt_token = jwt.encode(
        {"iat": int(time.time()) - 60, "exp": int(time.time()) + (10 * 60), "iss": app_id},
        private_key,
        algorithm="RS256",
    )
    print(f"handle: {app_id=} {secret=} {jwt_token=}")

    async with aiohttp.ClientSession() as session:
        # 2) Exchange JWT for installation token
        app_gh = GitHubAPI(session, "my-pr-status-bot", oauth_token=jwt_token)
        inst_id = event.data["installation"]["id"]
        token_resp = await get_installation_access_token(
            app_gh, installation_id=inst_id, app_id=app_id, private_key=private_key
        )
        inst_token = token_resp["token"]

        # 3) Dispatch with installation token
        gh = GitHubAPI(session, "my-pr-status-bot", oauth_token=inst_token)
        await router.dispatch(event, gh, inst_token)


async def process_async_event(event, router):
    """Authenticate, exchange tokens, and dispatch the event to the router."""
    app_id = os.getenv("GITHUB_APP_ID")
    private_key = Path(os.getenv("PRIVATE_KEY_PATH")).read_bytes()
    jwt_token = jwt.encode(
        {"iat": int(time.time()) - 60, "exp": int(time.time()) + (10 * 60), "iss": app_id},
        private_key,
        algorithm="RS256",
    )

    async with aiohttp.ClientSession() as session:
        # Exchange JWT for installation token
        app_gh = GitHubAPI(session, "my-pr-status-bot", oauth_token=jwt_token)
        inst_id = event.data["installation"]["id"]
        token_resp = await get_installation_access_token(
            app_gh, installation_id=inst_id, app_id=app_id, private_key=private_key
        )
        inst_token = token_resp["token"]

        # Dispatch with installation token
        gh = GitHubAPI(session, "my-pr-status-bot", oauth_token=inst_token)
        await router.dispatch(event, gh, inst_token)


async def handle_with_offloaded_tasks(request):
    """Minimal HTTP handler: read the webhook, schedule processing, and ack."""
    body = await request.read()
    secret = os.getenv("WEBHOOK_SECRET", "")
    event = sansio.Event.from_http(request.headers, body, secret=secret)

    # Get router from request app
    router = request.app["router"]

    # Schedule background processing with router
    asyncio.create_task(process_async_event(event, router))

    # Acknowledge immediately
    return web.Response(status=200)
