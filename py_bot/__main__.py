import logging
import os
from functools import partial
from pathlib import Path

from aiohttp import web
from gidgethub import routing

from py_bot.handling import handle_with_offloaded_tasks
from py_bot.on_event import on_code_changed


def _load_validate_required_env_vars() -> tuple[str, str, str]:
    """Ensure required environment variables are set."""
    github_app_id = os.getenv("GITHUB_APP_ID")
    assert github_app_id, "`GITHUB_APP_ID` must be set in environment variables"
    private_key = os.getenv("PRIVATE_KEY")
    if not private_key:
        # If PRIVATE_KEY is not set, read from a file specified by PRIVATE_KEY_PATH
        private_key_path = os.getenv("PRIVATE_KEY_PATH")
        assert private_key_path, "`PRIVATE_KEY_PATH` must be set in environment variables"
        private_key_path = Path(private_key_path).expanduser().resolve()
        assert private_key_path.is_file(), f"Private key file not found at {private_key_path}"
        private_key = private_key_path.read_text()
    assert private_key.startswith("-----BEGIN RSA PRIVATE KEY-----"), "Private key must be in PEM format"
    assert private_key.endswith("-----END RSA PRIVATE KEY-----"), "Private key must be in PEM format"
    webhook_secret = os.getenv("WEBHOOK_SECRET", "")
    return github_app_id, private_key, webhook_secret


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    github_app_id, private_key, webhook_secret = _load_validate_required_env_vars()
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
            github_app_id=github_app_id,
            private_key=private_key,
            webhooks_secret=webhook_secret,
        ),
    )

    logging.info("starting…")
    web.run_app(app, host="0.0.0.0", port=8080)
