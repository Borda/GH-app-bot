from aiohttp import web
from gidgethub import routing

from py_bot.handling import handle_webhook
from py_bot.on_event import on_pr_synchronize

if __name__ == "__main__":
    # Create router and register handlers
    router = routing.Router()
    router.add(on_pr_synchronize, event_type="pull_request", action="synchronize")

    # Create app and store router
    app = web.Application()
    app["router"] = router
    app.router.add_post("/", handle_webhook)

    print("starting...")
    web.run_app(app, port=8080)
