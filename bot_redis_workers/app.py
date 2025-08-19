import json

import redis
from aiohttp import ClientSession, web
from gidgethub import routing, sansio

from bot_commons.utils import _load_validate_required_env_vars
from bot_redis_workers import REDIS_URL


async def handle_pr_event(event, redis_client):
    payload = event.data
    task = {
        "type": "new_event",
        "payload": payload,
    }
    # Use the Redis client from app context
    redis_client.rpush("bot_queue", json.dumps(task))
    print(f"Enqueued new_event for PR #{task['pr_number']}")


async def main(request):
    # Load and validate all environment vars exactly once
    _, _, webhook_secret = _load_validate_required_env_vars()
    body = await request.read()
    app = request.app

    # Parse the webhook
    event = sansio.Event.from_http(request.headers, body, secret=webhook_secret)

    # GitHub ping → just ACK
    if event.event == "ping":
        return web.Response(status=200)

    # Dispatch to our handlers
    await app["router"].dispatch(event, app["redis_client"])
    return web.Response(status=200)


async def init_app():
    # Localize Redis client creation here
    redis_client = redis.from_url(REDIS_URL)

    # Create a GitHub routing router
    router = routing.Router()
    router.add(handle_pr_event, event_type="pull_request", action="synchronize")

    # Create the aiohttp application
    app = web.Application()
    app["http_session"] = ClientSession()
    # Store the Redis client and router in the app context
    app["redis_client"] = redis_client
    app["router"] = router

    app.router.add_post("/", main)
    return app


if __name__ == "__main__":
    # run_app accepts either an app instance or coroutine
    web.run_app(init_app(), host="0.0.0.0", port=8080, access_log=None)
