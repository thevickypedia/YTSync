import asyncio
import logging
import pathlib
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.routing import APIRoute

from yt2jf.config import env
from yt2jf.poll import run_polling, shutdown_event
from yt2jf.routes import (
    ACTIVE_TASKS,
    api_delete_webhook,
    api_get_webhook,
    api_set_webhook,
    telegram_webhook,
)
from yt2jf.version import __version__

LOGGER = logging.getLogger("uvicorn.default")


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Simple startup function to add anything that has to be triggered when Jarvis API starts up."""
    # noinspection HttpUrlsUsage
    LOGGER.info("Hosting at http://%s:%s", env.host, env.port)
    bg_task = None
    if not env.bot_webhook:
        LOGGER.info("Polling for incoming messages...")
        bg_task = asyncio.create_task(run_polling())
        ACTIVE_TASKS["poll"] = bg_task
    yield
    if bg_task:
        bg_task.cancel()
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

app = FastAPI(title="YT2JF", version=__version__, lifespan=lifespan, routes=routes)


def run():
    """Start the Jarvis API server using Uvicorn."""
    module_name = pathlib.Path(__file__)
    kwargs = dict(
        host=env.host,
        port=env.port,
        app=f"{module_name.parent.stem}.main:app",
    )
    if env.log_config:
        kwargs["log_config"] = env.log_config
    uvicorn.run(**kwargs)
