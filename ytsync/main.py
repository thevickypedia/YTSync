import asyncio
import logging
import pathlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone

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
    LOGGER.debug("Host: %s", config.env.host)
    LOGGER.debug("Port: %s", config.env.port)
    LOGGER.debug("Timezone: %s", config.env.tz)
    LOGGER.debug("Max retries: %d", config.env.max_retries)
    LOGGER.debug("Backoff factor: %d", config.env.backoff_factor)
    LOGGER.debug("Max transfers: %d", config.env.max_transfers)
    LOGGER.debug("Delayed start: %s", config.env.delayed_start)
    LOGGER.debug("Next buffer: %d", config.env.next_buffer)
    LOGGER.debug("Cooldown interval: %d", config.env.cooldown_interval)
    LOGGER.debug("Max error threshold: %d", config.env.max_error_threshold)
    LOGGER.debug("Data directory: %s", config.env.data_dir)
    LOGGER.debug("Logs directory: %s", config.env.logs_dir)
    LOGGER.debug("Download directory: %s", config.env.download_dir)
    LOGGER.debug("Database: %s", config.env.database)
    LOGGER.debug("Allowed bot IDs: %s", config.env.bot_chat_ids)
    LOGGER.debug("Allowed bot users: %s", config.env.bot_users)
    LOGGER.debug("Poll interval: %s", config.env.poll_interval)
    LOGGER.debug("Default tracker: %s", config.env.default_tracker)
    LOGGER.debug("Remote host: %s", config.env.remote_host)
    LOGGER.debug("Remote user: %s", config.env.remote_user)
    LOGGER.debug("Remote path: %s", config.env.remote_path)
    LOGGER.debug("Delete after sync: %s", config.env.delete_after_sync)
    LOGGER.debug("Bot webhook: %s", config.env.bot_webhook)
    LOGGER.debug("Bot webhook IP: %s", config.env.bot_webhook_ip)
    LOGGER.debug("Bot endpoint: %s", config.env.bot_endpoint)
    LOGGER.debug("Bot certificate: %s", config.env.bot_certificate)
    LOGGER.debug("***************************** CONFIGURATION END *****************************")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Simple startup function to add anything that has to be triggered when Jarvis API starts up."""
    # noinspection HttpUrlsUsage
    LOGGER.info("Hosting at http://%s:%s", config.env.host, config.env.port)
    if LOGGER.isEnabledFor(logging.DEBUG):
        log_config()
    LOGGER.info("Initiating background tasks...")
    bg_task = asyncio.create_task(agent.executor())
    if not webhook_is_usable():
        try:
            webhook.delete_webhook()
        except requests.RequestException:
            pass
        poll.start_polling()
    yield
    await poll.stop_polling()
    bg_task.cancel()
    poll.shutdown_event()
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
        methods=["POST"],
        path="/add-trackers",
    ),
    APIRoute(
        endpoint=routes.api_delete_trackers,
        methods=["DELETE"],
        path="/delete-trackers",
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
