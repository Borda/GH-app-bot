import logging
from functools import partial

from aiohttp import web
from gidgethub import routing

from bot_async_tasks.handling import handle_with_offloaded_tasks
from bot_async_tasks.on_event import on_code_changed
from bot_commons.utils import _load_validate_required_env_vars

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    github_app_id, private_key, webhook_secret = _load_validate_required_env_vars()
    # Create router and register handlers
    router = routing.Router()
    # todo: register also re-run event
    router.add(on_code_changed, event_type="pull_request", action="synchronize")
    router.add(on_code_changed, event_type="push")

    # Create app and store router
    app = web.Application()
    app["router"] = router
    app.router.add_post(
        "/",
        partial(
            handle_with_offloaded_tasks,
            github_app_id=github_app_id,
            private_key=private_key,
            webhooks_secret=webhook_secret,
        ),
    )

    logging.info("starting…")
    web.run_app(app, host="0.0.0.0", port=8080, access_log=None)
