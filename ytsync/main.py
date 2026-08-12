import logging
import pathlib
from contextlib import asynccontextmanager

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
from ytsync.webhook import get_webhook

LOGGER = logging.getLogger("ytsync")


def webhook_is_available() -> bool:
    """Check if there is an existing webhook.

    Returns:
        bool:
        Returns a boolean flag to indicate webhook availability.
    """
    # TODO: There are possibilities of SSL errors that will leave the API in an un-reachable state
    #   This logic only checks for the presence of a URL
    #   1. Look for last_error key and check the timestamp of the occurrence
    #   2. Create an intential circular dependency to send a message as a user and confirm it's receival node
    #   Not sure if that's even possible OR an overkill
    try:
        webhook = get_webhook() or {}
        result = webhook.get("result", {}) or {}
        if isinstance(result, dict):
            return bool(result.get("url"))
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
