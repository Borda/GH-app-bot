import json

import redis
from aiohttp import ClientSession, web
from gidgethub import routing, sansio

from bot_redis_workers import REDIS_URL


def make_router():
    router = routing.Router()

    @router.register("pull_request", action=("opened", "synchronize"))
    async def handle_pr_event(event, redis_client):
        payload = event.data
        task = {
            "type": "new_event",
            "payload": payload,
            "installation_id": payload["installation"]["id"],
            "repo_full_name": payload["repository"]["full_name"],
            "pr_number": payload["pull_request"]["number"],
        }
        # Use the Redis client from app context
        redis_client.rpush("bot_queue", json.dumps(task))
        print(f"Enqueued new_event for PR #{task['pr_number']}")

    return router


async def main(request):
    body = await request.read()
    app = request.app

    # Parse the webhook
    event = sansio.Event.from_http(request.headers, body, secret=app["webhook_secret"])

    # GitHub ping → just ACK
    if event.event == "ping":
        return web.Response(status=200)

    # Dispatch to our handlers
    await app["router"].dispatch(event, app["redis_client"])
    return web.Response(status=200)


async def init_app():
    # Localize Redis client creation here
    redis_client = redis.from_url(REDIS_URL)

    app = web.Application()
    app["http_session"] = ClientSession()
    app["redis_client"] = redis_client
    app["router"] = make_router()

    app.router.add_post("/", main)

    return app


if __name__ == "__main__":
    # run_app accepts either an app instance or coroutine
    web.run_app(init_app(), host="0.0.0.0", port=8080, access_log=None)
