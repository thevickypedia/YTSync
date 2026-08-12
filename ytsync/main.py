import logging
import pathlib
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import requests
import uvicorn
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.routing import APIRoute

from ytsync.config import env
from ytsync.poll import shutdown_event, start_polling, stop_polling
from ytsync.routes import (
    api_delete_webhook,
    api_get_webhook,
    api_set_webhook,
    telegram_webhook,
)
from ytsync.version import __version__
from ytsync.webhook import delete_webhook, get_webhook

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
        webhook = get_webhook() or {}
        result = webhook.get("result", {}) or {}
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


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Simple startup function to add anything that has to be triggered when Jarvis API starts up."""
    # noinspection HttpUrlsUsage
    LOGGER.info("Hosting at http://%s:%s", env.host, env.port)
    if not webhook_is_usable():
        try:
            delete_webhook()
        except requests.RequestException:
            pass
        start_polling()
    yield
    await stop_polling()
    shutdown_event()
    LOGGER.info("Shutting down API server.")


async def docs_redirect() -> RedirectResponse:
    """Redirect the root path to the ``/docs`` page."""
    return RedirectResponse("/docs")


routes = [
    APIRoute(
        endpoint=telegram_webhook,
        methods=["POST"],
        path=env.bot_endpoint,
        include_in_schema=False,
    ),
    APIRoute(
        endpoint=docs_redirect,
        methods=["GET"],
        path="/",
        include_in_schema=False,
    ),
    APIRoute(
        endpoint=api_get_webhook,
        methods=["GET"],
        path="/get-webhook",
    ),
    APIRoute(
        endpoint=api_set_webhook,
        methods=["POST"],
        path="/set-webhook",
    ),
    APIRoute(
        endpoint=api_delete_webhook,
        methods=["DELETE"],
        path="/delete-webhook",
    ),
]

app = FastAPI(title="YTSync", version=__version__, lifespan=lifespan, routes=routes)


def start():
    """Start the Jarvis API server using Uvicorn."""
    module_name = pathlib.Path(__file__)
    kwargs = dict(
        host=env.host,
        port=env.port,
        app=f"{module_name.parent.stem}.main:app",
        workers=1,
    )
    if env.log_config:
        kwargs["log_config"] = env.log_config
    uvicorn.run(**kwargs)
