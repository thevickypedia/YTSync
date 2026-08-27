import asyncio
import logging
import pathlib
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from multiprocessing.pool import ThreadPool

import requests
import uvicorn
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.routing import APIRoute

from ytsync.api import routes
from ytsync.crontab import agent
from ytsync.modules import config
from ytsync.telegram import poll, webhook
from ytsync.version import __version__

LOGGER = logging.getLogger("ytsync")


def circular_healthcheck() -> bool:
    """Health check function to check if the API is running."""
    try:
        # 2s timeout is pretty generous, considering the fact that this is a circular call to the API hosted
        response = requests.post(
            config.env.bot_webhook,
            json={"healthcheck": True},
            timeout=(2, 2),
            headers={"X-Telegram-Bot-Api-Secret-Token": config.env.bot_secret},
        )
        response.raise_for_status()
    except requests.RequestException as error:
        LOGGER.exception(error)
        return False
    return response.status_code == 200


def webhook_is_usable() -> bool:
    """Check if there is an existing webhook.

    Examples:
        - Successful:

        .. code-block:: json

            {
              "ok": true,
              "result": {
                "url": "https://ytsync.example.com/telegram-webhook",
                "has_custom_certificate": false,
                "pending_update_count": 0,
                "max_connections": 40,
                "ip_address": "198.51.100.42"
              }
            }

        - Unsuccessful:

        .. code-block:: json

            {
              "ok": true,
              "result": {
                "url": "https://ytsync.example.com/telegram-webhook",
                "has_custom_certificate": false,
                "pending_update_count": 1,
                "last_error_date": 1786496692,
                "last_error_message": "Connection refused",
                "max_connections": 40,
                "ip_address": "198.51.100.42"
              }
            }

    Returns:
        bool:
        Returns a boolean flag to indicate webhook availability.
    """
    # 1. Set the webhook and verify it's working
    if all((config.env.bot_webhook, config.env.bot_secret, config.env.bot_webhook_ip)):
        webhook.set_webhook(
            webhook=config.env.bot_webhook,
            secret_token=config.env.bot_secret,
            webhook_ip=config.env.bot_webhook_ip,
        )
        time.sleep(30)  # Wait for the webhook to be set up
        thread = ThreadPool(processes=1).apply_async(circular_healthcheck)
        if thread.get(timeout=5):
            LOGGER.info("Webhook healthcheck passed, assuming usable...")
            return True
        LOGGER.warning("Webhook healthcheck failed, deleting it...")
        webhook.delete_webhook()
        return False
    # 2. Check if there is one setup and look for the error count and how old they were
    max_pending_updates = 100
    max_error_age_seconds = 60
    try:
        existing_webhook = webhook.get_webhook() or {}
        result = existing_webhook.get("result", {}) or {}
        assert isinstance(result, dict), f"Invalid result object received: {result}"
    except (AssertionError, requests.RequestException) as error:
        LOGGER.warning(error)
        return False
    LOGGER.debug(result)
    # NOTE: This exception handler is only a guard rail, just in case if the payload type changes
    try:
        # Set age to a value higher than the max_error_age if last_error_date is nil
        last_error_date = int(result.get("last_error_date", max_error_age_seconds + 1))
        # Set pending update count to a value lower than the max_pending_updates if pending_update_count is nil
        pending_update_count = int(result.get("pending_update_count", max_pending_updates - 1))
    except (TypeError, ValueError) as error:
        LOGGER.critical(error)
        return False
    last_error_timestamp = datetime.fromtimestamp(
        last_error_date,
        tz=timezone.utc,
    )
    age = (datetime.now(timezone.utc) - last_error_timestamp).total_seconds()
    # 1. Check 'url' to ensure webhook is configured
    # 2. Check if 'pending_update_count' is less than the max expectation
    # 3. Check if the 'last_error_date' is within the accepted timestamp
    return bool(result.get("url") and pending_update_count < max_pending_updates and age > max_error_age_seconds)


def log_config() -> None:
    """Log all safe env configuration."""
    LOGGER.debug("***************************** CONFIGURATION START *****************************")
    sensitive = ("log_config", "bot_token", "bot_secret", "apikey", "bot_users", "bot_chat_ids")
    for key, value in config.env.model_dump().items():
        if key in sensitive:
            continue
        key = key.capitalize().replace("_", " ").replace("dir", "directory")
        LOGGER.debug("%s: %s", key, value)
    LOGGER.debug("***************************** CONFIGURATION END *****************************")


def webhook_manager() -> None:
    """Manage the webhook setup and polling."""
    LOGGER.info("Starting webhook manager...")
    if not webhook_is_usable():
        try:
            webhook.delete_webhook()
        except requests.RequestException:
            pass
        poll.start_polling()


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Simple startup function to add anything that has to be triggered when Jarvis API starts up."""
    # noinspection HttpUrlsUsage
    LOGGER.info("Hosting at http://%s:%s", config.env.host, config.env.port)
    if LOGGER.isEnabledFor(logging.DEBUG):
        log_config()
    LOGGER.info("Initiating background tasks...")
    bg_task = asyncio.create_task(agent.executor())
    timer = threading.Timer(interval=10, function=webhook_manager)
    timer.start()
    yield
    # Stop the timer, in case the server is shutdown before the timer is done
    timer.join(timeout=3)
    if timer.is_alive():
        LOGGER.warning("Webhook manager is still running, cancelling it...")
        timer.cancel()
    LOGGER.info("Webhook manager has been cancelled")
    # Stop the polling task (if any)
    await poll.stop_polling()
    # Stop the background task
    bg_task.cancel()
    # Clear the process pool
    agent.shutdown_event()
    LOGGER.info("Shutting down API server.")


async def docs_redirect() -> RedirectResponse:
    """Redirect the root path to the ``/docs`` page."""
    return RedirectResponse("/docs")


api_routes = [
    APIRoute(
        endpoint=routes.telegram_webhook,
        methods=["POST"],
        path=config.env.bot_endpoint,
        include_in_schema=False,
    ),
    APIRoute(
        endpoint=docs_redirect,
        methods=["GET"],
        path="/",
        include_in_schema=False,
    ),
    APIRoute(
        endpoint=routes.api_get_webhook,
        methods=["GET"],
        path="/get-webhook",
    ),
    APIRoute(
        endpoint=routes.api_set_webhook,
        methods=["POST"],
        path="/set-webhook",
    ),
    APIRoute(
        endpoint=routes.api_delete_webhook,
        methods=["DELETE"],
        path="/delete-webhook",
    ),
    APIRoute(
        endpoint=routes.api_get_trackers,
        methods=["GET"],
        path="/get-trackers",
    ),
    APIRoute(
        endpoint=routes.api_add_trackers,
        methods=["PUT"],
        path="/add-trackers",
    ),
    APIRoute(
        endpoint=routes.api_delete_trackers,
        methods=["DELETE"],
        path="/delete-trackers",
    ),
    APIRoute(
        endpoint=routes.download,
        methods=["POST"],
        path="/download",
    ),
]

app = FastAPI(title="YTSync", version=__version__, lifespan=lifespan, routes=api_routes)


def start():
    """Start the Jarvis API server using Uvicorn."""
    module_name = pathlib.Path(__file__)
    kwargs = dict(
        host=config.env.host,
        port=config.env.port,
        app=f"{module_name.parent.stem}.main:app",
        workers=1,
    )
    if config.env.log_config:
        kwargs["log_config"] = config.env.log_config
    uvicorn.run(**kwargs)
