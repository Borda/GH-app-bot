import asyncio
import json

import redis
from aiohttp import ClientSession, web
from gidgethub import routing, sansio
from gidgethub.aiohttp import GitHubAPI
from gidgethub.apps import get_installation_access_token

from bot_commons.utils import _load_validate_required_env_vars, create_jwt_token
from bot_redis_workers import REDIS_URL

redis_client = redis.from_url(REDIS_URL)
router = routing.Router()


# @router.register("push")
@router.register("pull_request", action="opened")
@router.register("pull_request", action="synchronize")
async def handle_pr_event(event, gh, *args, **kwargs):
    # Enqueue the new event task
    payload = event.data
    task = {
        "type": "new_event",
        "payload": payload,
        "installation_id": payload["installation"]["id"],
        "repo_full_name": payload["repository"]["full_name"],
        "pr_number": payload["pull_request"]["number"],
    }
    redis_client.rpush("bot_queue", json.dumps(task))
    print(f"Enqueued new_event for PR #{task['pr_number']}")


async def main(request):
    """Handle requests from GitHub."""
    github_app_id, private_key, webhook_secret = _load_validate_required_env_vars()
    body = await request.read()
    event = sansio.Event.from_http(request.headers, body, secret=webhook_secret)
    installation_id = event.data["installation"]["id"]
    jwt_token = create_jwt_token(github_app_id=github_app_id, app_private_key=private_key)

    gh = GitHubAPI(request.app["http_session"], "bot_redis_workers", oauth_token=jwt_token)
    access_token = await get_installation_access_token(
        gh, installation_id=installation_id, app_id=str(github_app_id), private_key=private_key
    )
    gh.oauth_token = access_token["token"]
    await router.dispatch(event, gh)

    return web.Response(status=200)


async def init_app():
    app = web.Application()
    app.router.add_post("/", main)
    app["http_session"] = ClientSession()
    return app


if __name__ == "__main__":
    app = asyncio.run(init_app())
    web.run_app(app, port=8080)  # Or your preferred port
