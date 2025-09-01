import asyncio
import logging

from aiohttp import ClientSession, web
from gidgethub import routing, sansio
from gidgethub.aiohttp import GitHubAPI
from gidgethub.apps import get_installation_access_token

from _bots_commons.gh_posts import create_jwt_token
from _bots_commons.utils import load_validate_required_env_vars

# async def handle_webhook(request):
#     print("=== webhook hit ===")
#     body = await request.read()
#     secret = os.getenv("WEBHOOK_SECRET", "")
#     event = sansio.Event.from_http(request.headers, body, secret=secret)
#
#     # Get router from app
#     router = request.app["router"]
#
#     # 1) App authentication → JWT
#     app_id = os.getenv("GITHUB_APP_ID")
#     with open(os.getenv("PRIVATE_KEY_PATH"), "rb") as fp:
#         private_key = fp.read()  # Read the private key from the file
#     jwt_token = jwt.encode(
#         {"iat": int(time.time()) - 60, "exp": int(time.time()) + (10 * 60), "iss": app_id},
#         private_key,
#         algorithm="RS256",
#     )
#     print(f"handle: {app_id=} {secret=} {jwt_token=}")
#
#     async with aiohttp.ClientSession() as session:
#         # 2) Exchange JWT for installation token
#         app_gh = GitHubAPI(session, "my-pr-status-bot", oauth_token=jwt_token)
#         inst_id = event.data["installation"]["id"]
#         token_resp = await get_installation_access_token(
#             app_gh, installation_id=inst_id, app_id=app_id, private_key=private_key
#         )
#         inst_token = token_resp["token"]
#
#         # 3) Dispatch with installation token
#         gh = GitHubAPI(session, "my-pr-status-bot", oauth_token=inst_token)
#         await router.dispatch(event, gh, inst_token)


async def process_async_event(
    event: sansio.Event, router: routing.Router, github_app_id: int, app_private_key: str
) -> None:
    """Authenticate with GitHub App, swap for installation token, and dispatch.

    Args:
        event: Parsed GitHub webhook event.
        router: Router responsible for dispatching the event to handlers.
        github_app_id: GitHub App ID.
        app_private_key: PEM-encoded private key used to sign the JWT.
    """
    jwt_token = create_jwt_token(github_app_id=github_app_id, app_private_key=app_private_key)

    async with ClientSession() as session:
        # Exchange JWT for installation token
        app_gh = GitHubAPI(session, "bot_async_tasks", oauth_token=jwt_token)
        installation_id = event.data["installation"]["id"]
        token_resp = await get_installation_access_token(
            app_gh, installation_id=installation_id, app_id=str(github_app_id), private_key=app_private_key
        )
        inst_token = token_resp["token"]

        # Dispatch with installation token
        gh = GitHubAPI(session, "bot_async_tasks", oauth_token=inst_token)
        await router.dispatch(event, gh, inst_token)


async def handle_request(request: web.Request) -> web.Response:
    """Read webhook, schedule async processing, and acknowledge immediately.

    Args:
        request: Incoming aiohttp request containing the GitHub webhook.

    Returns:
        An HTTP response acknowledging receipt (200) or an error (400).
    """
    github_app_id, app_private_key, webhook_secret = load_validate_required_env_vars()

    try:
        # Read the raw body, handling client disconnects
        body = await request.read()
    except ConnectionResetError:
        logging.warning("Client disconnected before request body was fully read")
        return web.Response(status=400)

    try:
        # Parse the GitHub webhook event, validating signature
        event = sansio.Event.from_http(request.headers, body, secret=webhook_secret)
    except Exception as exc:
        logging.error("Failed to parse webhook event", exc_info=exc)
        return web.Response(status=400)

    # Get the router from the app context
    router = request.app["router"]

    # Offload event processing to a background task
    asyncio.create_task(
        process_async_event(event, router, github_app_id=github_app_id, app_private_key=app_private_key)
    )

    # Immediately acknowledge receipt
    return web.Response(status=200)
