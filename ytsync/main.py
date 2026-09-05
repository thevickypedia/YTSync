import asyncio
import logging
import pathlib
from contextlib import asynccontextmanager
from typing import Dict

import uvicorn
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.routing import APIRoute

from ytsync.api import routes
from ytsync.crontab import agent
from ytsync.modules import config
from ytsync.version import __version__

LOGGER = logging.getLogger("ytsync")


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


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Simple startup function to add anything that has to be triggered when Jarvis API starts up."""
    # noinspection HttpUrlsUsage
    if LOGGER.isEnabledFor(logging.DEBUG):
        log_config()
    LOGGER.info("Initiating background tasks...")
    bg_task = asyncio.create_task(agent.executor())
    yield
    # Stop the background task
    bg_task.cancel()
    # Clear the process pool
    agent.shutdown_event()
    LOGGER.info("Shutting down API server.")


async def docs_redirect() -> RedirectResponse:
    """Redirect the root path to the ``/docs`` page."""
    return RedirectResponse("/docs")


async def health() -> Dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


api_routes = [
    APIRoute(
        endpoint=health,
        methods=["GET"],
        path="/health",
        include_in_schema=False,
    ),
    APIRoute(
        endpoint=docs_redirect,
        methods=["GET"],
        path="/",
        include_in_schema=False,
    ),
    APIRoute(
        endpoint=routes.telegram_webhook,
        methods=["POST"],
        path=config.env.bot_endpoint,
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
    APIRoute(
        endpoint=routes.list_checkpoints,
        methods=["GET"],
        path="/list-checkpoints",
    ),
    APIRoute(
        endpoint=routes.get_checkpoint,
        methods=["GET"],
        path="/get-checkpoint",
    ),
]

app = FastAPI(title="YTSync", version=__version__, lifespan=lifespan, routes=api_routes)


def start():
    """Start the Jarvis API server using uvicorn."""
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
