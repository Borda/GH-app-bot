import logging
import os
from functools import partial
from pathlib import Path

from aiohttp import web
from gidgethub import routing

from py_bot.handling import handle_with_offloaded_tasks
from py_bot.on_event import on_code_changed

GITHUB_APP_ID = os.getenv("GITHUB_APP_ID")
_PRIVATE_KEY_PATH = os.getenv("PRIVATE_KEY_PATH")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    assert GITHUB_APP_ID, "`GITHUB_APP_ID` must be set in environment variables"
    assert _PRIVATE_KEY_PATH, "`PRIVATE_KEY_PATH` must be set in environment variables"
    private_key_path = Path(_PRIVATE_KEY_PATH).expanduser().resolve()
    assert private_key_path.is_file(), f"Private key file not found at {private_key_path}"
    # Create router and register handlers
    router = routing.Router()
    router.add(on_code_changed, event_type="pull_request", action="synchronize")
    router.add(on_code_changed, event_type="push")

    # Create app and store router
    app = web.Application()
    app["router"] = router
    app.router.add_post(
        "/",
        partial(
            handle_with_offloaded_tasks,
            github_app_id=GITHUB_APP_ID,
            private_key=private_key_path.read_text(),
            webhooks_secret=WEBHOOK_SECRET,
        ),
    )

    logging.info("starting…")
    web.run_app(app, host="0.0.0.0", port=8080)
