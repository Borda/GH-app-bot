import os

import aiohttp
from aiohttp import web
from gidgethub import routing, sansio
from gidgethub.aiohttp import GitHubAPI

routes = web.RouteTableDef()
router = routing.Router()


@router.register("issues", action="opened")
async def issue_opened_event(event, gh, *args, **kwargs):
    """Whenever an issue is opened, greet the author and say thanks."""
    print("-> issues: opened")
    url = event.data["issue"]["comments_url"]
    author = event.data["issue"]["user"]["login"]
    message = f"Thanks for the report @{author}! I will look into it ASAP! (I'm a bot)."
    await gh.post(url, data={"body": message})


@router.register("pull_request", action="closed")
async def pull_request_closed_event(event, gh, *args, **kwargs):
    """Whenever an pull_request is closed, greet the author and say thanks."""
    print("-> pull_request: closed")
    url = event.data["pull_request"]["comments_url"]
    author = event.data["pull_request"]["user"]["login"]
    if event.data["pull_request"]["merged"]:
        message = (
            f"Thanks @{author} for creating this pull request!"
            " Feel free to create more pull requests and improve our repository (I'm a bot)."
        )
    else:
        message = f"Thanks @{author} for creating this pull request! We are closing this pull request (I'm a bot)."
    await gh.post(url, data={"body": message})


@routes.post("/")
async def main(request):
    body = await request.read()

    # secret = os.environ.get("GH_SECRET")
    oauth_token = os.environ.get("GH_AUTH")

    event = sansio.Event.from_http(request.headers, body)  # , secret=secret
    async with aiohttp.ClientSession() as session:
        gh = GitHubAPI(session, "your-bot-name", oauth_token=oauth_token)
        await router.dispatch(event, gh)
    return web.Response(status=200)


if __name__ == "__main__":
    app = web.Application()
    app.add_routes(routes)
    port = os.environ.get("PORT")
    if port is not None:
        port = int(port)

    web.run_app(app, port=port)
