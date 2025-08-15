"""Main entry point for the GitHub Actions bot with Redis queue."""

import logging
import os
from pathlib import Path
from functools import partial

from aiohttp import web

from .webhook_handler import create_webhook_handler


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

    webhook_secret = os.getenv("WEBHOOK_SECRET", "")

    return github_app_id, private_key, webhook_secret


def create_app() -> web.Application:
    """Create and configure the aiohttp application."""
    # Load environment variables
    github_app_id, private_key, webhook_secret = _load_validate_required_env_vars()

    # Create application
    app = web.Application()

    # Add webhook endpoint
    webhook_handler = create_webhook_handler(github_app_id, private_key, webhook_secret)
    app.router.add_post("/webhook", webhook_handler)
    app.router.add_post("/", webhook_handler)  # Default webhook path

    # Add health check endpoint
    async def health_check(request: web.Request) -> web.Response:
        return web.Response(text="OK", status=200)

    app.router.add_get("/health", health_check)

    # Store config in app for potential use by other handlers
    app["config"] = {
        "github_app_id": github_app_id,
        "webhook_secret": webhook_secret,
    }

    return app


def main():
    """Main function to run the web server."""
    # Configure logging
    logging.basicConfig(
        level=logging.INFO, 
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    logger = logging.getLogger(__name__)

    # Get configuration
    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8080"))

    # Create and configure app
    app = create_app()

    logger.info(f"Starting GitHub Actions bot with Redis queue on {host}:{port}")
    logger.info("Available endpoints:")
    logger.info("  POST / or /webhook - GitHub webhook handler")
    logger.info("  GET /health - Health check endpoint")

    # Start the server
    web.run_app(app, host=host, port=port, access_log=None)


if __name__ == "__main__":
    main()
