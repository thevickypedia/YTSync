import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Dict

import httpx
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from fastapi.routing import APIRoute

from ytsync.api import models, routes
from ytsync.crontab import agent
from ytsync.modules import config

LOGGER = logging.getLogger("ytsync")


async def log_config() -> None:
    """Log all safe env configuration."""
    LOGGER.debug("***************************** CONFIGURATION START *****************************")
    sensitive = ("log_config", "bot_token", "bot_secret", "profiles")
    LOGGER.debug("Profile(s) allowed: %s", [profile.name for profile in config.env.profiles])
    for key, value in config.env.model_dump().items():
        if key in sensitive:
            continue
        key = key.capitalize().replace("_", " ").replace("dir", "directory")
        LOGGER.debug("%s: %s", key, value)
    LOGGER.debug("***************************** CONFIGURATION END *****************************")


def bg_task_callback(task: asyncio.Task) -> None:
    """Callback for background tasks.

    Args:
        task: Takes the async task object as a parameter.
    """
    name = task.get_name()
    try:
        result = task.result()
        LOGGER.info("Background task [%s] completed successfully", name)
        LOGGER.info(result)
    except (asyncio.CancelledError, KeyboardInterrupt):
        LOGGER.debug("Terminated due to event cancellation.")
    except Exception as error:
        LOGGER.exception(error)
        LOGGER.error("Background task [%s] failed to finish", name)


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Startup and shutdown events for the FastAPI application."""
    # noinspection HttpUrlsUsage
    if LOGGER.isEnabledFor(logging.DEBUG):
        await log_config()
    LOGGER.info("Initiating background tasks...")
    bg_task = asyncio.create_task(agent.executor())
    bg_task.add_done_callback(bg_task_callback)
    async with httpx.AsyncClient() as client:
        # SET: app.state.http_client = client
        # USE: client: httpx.AsyncClient = request.app.state.http_client
        config.ASYNC_CLIENT = client
        config.MAIN_EVENT_LOOP = asyncio.get_running_loop()
        yield
    # Stop the background task
    bg_task.cancel()
    LOGGER.info("Shutting down API server.")


async def docs_redirect() -> RedirectResponse:
    """Redirect the root path to the ``/docs`` page."""
    return RedirectResponse("/docs")


async def health() -> Dict[str, str]:
    """Health check endpoint."""
    return {"status": "ok"}


async def version() -> str:
    """Version endpoint."""
    return config.API.version


api_routes = [
    # Hidden endpoints
    APIRoute(
        endpoint=docs_redirect,
        methods=["GET"],
        path="/",
        include_in_schema=False,
    ),
    APIRoute(
        endpoint=health,
        methods=["GET"],
        path="/health",
        include_in_schema=False,
    ),
    APIRoute(
        endpoint=version,
        methods=["GET"],
        path="/version",
        include_in_schema=False,
    ),
    APIRoute(
        endpoint=routes.telegram_webhook,
        methods=["POST"],
        path=config.env.bot_endpoint,
        include_in_schema=False,
    ),
    # YTSync download endpoint
    APIRoute(
        endpoint=routes.download,
        methods=["POST"],
        path="/download",
        tags=[models.Tags.YTSync.name],
    ),
    # Webhook endpoints
    APIRoute(
        endpoint=routes.api_get_webhook,
        methods=["GET"],
        path="/webhook",
        tags=[models.Tags.Webhook.name],
    ),
    APIRoute(
        endpoint=routes.api_set_webhook,
        methods=["POST"],
        path="/webhook",
        tags=[models.Tags.Webhook.name],
    ),
    APIRoute(
        endpoint=routes.api_delete_webhook,
        methods=["DELETE"],
        path="/webhook",
        tags=[models.Tags.Webhook.name],
    ),
    # Tracker endpoints
    APIRoute(
        endpoint=routes.api_get_trackers,
        methods=["GET"],
        path="/trackers",
        tags=[models.Tags.Tracker.name],
    ),
    APIRoute(
        endpoint=routes.api_add_trackers,
        methods=["PUT"],
        path="/trackers",
        tags=[models.Tags.Tracker.name],
    ),
    APIRoute(
        endpoint=routes.api_delete_trackers,
        methods=["DELETE"],
        path="/trackers",
        tags=[models.Tags.Tracker.name],
    ),
    # Checkpoint endpoints
    APIRoute(
        endpoint=routes.list_checkpoints,
        methods=["GET"],
        path="/checkpoints",
        tags=[models.Tags.Checkpoint.name],
    ),
    APIRoute(
        endpoint=routes.get_checkpoint,
        methods=["GET"],
        path="/checkpoint",
        tags=[models.Tags.Checkpoint.name],
    ),
    APIRoute(
        endpoint=routes.delete_checkpoint,
        methods=["DELETE"],
        path="/checkpoint",
        tags=[models.Tags.Checkpoint.name],
    ),
    # Queue endpoints
    APIRoute(
        endpoint=routes.get_queue,
        methods=["GET"],
        path="/queue",
        tags=[models.Tags.Queue.name],
    ),
    APIRoute(
        endpoint=routes.add_queue,
        methods=["PUT"],
        path="/queue",
        tags=[models.Tags.Queue.name],
    ),
    APIRoute(
        endpoint=routes.delete_queue,
        methods=["DELETE"],
        path="/queue",
        tags=[models.Tags.Queue.name],
    ),
]
