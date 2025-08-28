import json
import logging

import redis
from aiohttp import ClientSession, web
from gidgethub import routing, sansio

from _bots_commons.utils import _load_validate_required_env_vars, extract_repo_details
from bot_redis_workers import REDIS_QUEUE, REDIS_URL
from bot_redis_workers.tasks import TaskPhase


async def handle_code_event(event, redis_client):
    payload = event.data
    task = {
        "delivery_id": event.delivery_id,
        "event_type": event.event,
        "phase": TaskPhase.NEW_EVENT.value,
        "payload": payload,
    }
    # Use the Redis client from app context
    repo_owner, repo_name, head_sha, _ = extract_repo_details(event.event, payload)
    # skip if the SHA is ful of zeros
    if set(head_sha) == {"0"}:
        logging.warning(f"Skipping event for {repo_owner}/{repo_name} with zero SHA: {head_sha}")
        return
    redis_client.rpush(REDIS_QUEUE, json.dumps(task))
    if event.event == "pull_request":
        pr_number = payload["pull_request"]["number"]
        logging.info(f"Enqueued new_event for `PR` \t{repo_owner}/{repo_name}#{pr_number}")
    elif event.event == "push":
        logging.info(f"Enqueued new_event for `push` \t{repo_owner}/{repo_name}@{head_sha[:7]}")


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
    try:
        redis_client = redis.from_url(REDIS_URL)
    except Exception as ex:
        logging.error(f"Failed to connect to Redis at {REDIS_URL}: {ex}")
        raise

    # Create a GitHub routing router
    router = routing.Router()
    router.add(handle_code_event, event_type="push")
    router.add(handle_code_event, event_type="pull_request", action="synchronize")

    # Create the aiohttp application
    app = web.Application()
    app["http_session"] = ClientSession()
    # Store the Redis client and router in the app context
    app["redis_client"] = redis_client
    app["router"] = router

    app.router.add_post("/", main)
    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    # run_app accepts either an app instance or coroutine
    web.run_app(init_app(), host="0.0.0.0", port=8080, access_log=None)
