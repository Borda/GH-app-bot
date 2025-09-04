import logging

from aiohttp import web
from gidgethub import routing

from bot_async_tasks.handle_event import handle_request
from bot_async_tasks.on_event import on_code_changed

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    # Create a router and register handlers
    router = routing.Router()
    # todo: register also re-run event
    router.add(on_code_changed, event_type="pull_request", action="opened")
    router.add(on_code_changed, event_type="pull_request", action="synchronize")
    router.add(on_code_changed, event_type="push")

    # Create an app and store router
    app = web.Application()
    app["router"] = router
    app.router.add_post("/", handle_request)

    logging.info("starting…")
    web.run_app(app, host="0.0.0.0", port=8080, access_log=None)
