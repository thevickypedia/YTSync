import logging
import pathlib
from contextlib import asynccontextmanager

import requests
import uvicorn
from fastapi import FastAPI
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
from ytsync.webhook import get_webhook

LOGGER = logging.getLogger("ytsync")


def webhook_is_available() -> bool:
    """Check if there is an existing webhook.

    Returns:
        bool:
        Returns a boolean flag to indicate webhook availability.
    """
    try:
        return bool(get_webhook().get("result", {}).get("url"))
    except requests.RequestException as error:
        LOGGER.warning(error)
        return False


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Simple startup function to add anything that has to be triggered when Jarvis API starts up."""
    # noinspection HttpUrlsUsage
    LOGGER.info("Hosting at http://%s:%s", env.host, env.port)
    if not webhook_is_available():
        start_polling()
    yield
    await stop_polling()
    shutdown_event()
    LOGGER.info("Shutting down API server.")


routes = [
    APIRoute(
        endpoint=telegram_webhook,
        methods=["POST"],
        path=env.bot_endpoint,  # No enum
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
