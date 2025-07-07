import os
import time

import aiohttp
import jwt  # PyJWT
from aiohttp import web
from gidgethub import routing, sansio
from gidgethub.aiohttp import GitHubAPI
from gidgethub.apps import get_installation_access_token

from py_bot.on_event import on_pr_sync_simple


async def handle(request):
    print("=== webhook hit ===")
    body = await request.read()
    secret = os.getenv("WEBHOOK_SECRET", "")
    event = sansio.Event.from_http(request.headers, body, secret=secret)

    # 1) App authentication → JWT
    app_id = os.getenv("APP_ID")
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
        router = request.app["router"]
        await router.dispatch(event, gh)

    return web.Response(status=200)


if __name__ == "__main__":
    print("starting...")

    # Create router and register handlers
    router = routing.Router()
    router.add(on_pr_sync_simple, event_type="pull_request", action="synchronize")

    # Create app and store router
    app = web.Application()
    app["router"] = router
    app.router.add_post("/", handle)

    web.run_app(app, port=8080)
