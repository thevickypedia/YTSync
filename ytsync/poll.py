import asyncio
import logging
from typing import Dict

import requests.exceptions

from ytsync.bot import poll_for_messages
from ytsync.config import env
from ytsync.exceptions import (
    BotInUse,
    BotTokenInvalid,
    BotWebhookConflict,
    EgressErrors,
)
from ytsync.youtube import controllers, process_pool

LOGGER = logging.getLogger("uvicorn.default")
ACTIVE_TASKS: Dict[str, asyncio.Task] = {}


def shutdown_event():
    """Shuts down all the threads and gracefully terminates the processes."""
    process_pool.shutdown(wait=True)
    for controller in controllers:
        LOGGER.info("Shutting down controller for: %s", controller.name)
        try:
            result = controller.future.result()
        except Exception as exc:
            LOGGER.error("Controller failed for %s: %s", controller.name, exc)
        else:
            LOGGER.info("Controller completed for %s: %s", controller.name, result)


async def stop_polling() -> None:
    """Stop polling for incoming messages."""
    task = ACTIVE_TASKS.pop("poll", None)
    if task and not task.done():
        LOGGER.info("Stopping long poll")
        task.cancel("polling stopped")
        try:
            await task
        except asyncio.CancelledError:
            pass


def start_polling() -> None:
    """Start polling for incoming messages."""
    task = ACTIVE_TASKS.get("poll")
    if task and not task.done():
        LOGGER.warning("Polling task already running")
        return
    LOGGER.info("Polling for incoming messages...")
    ACTIVE_TASKS["poll"] = asyncio.create_task(run_polling())


async def run_polling():
    """Starts up all the threads and gracefully terminates the processes."""
    offset = 0
    failed_connections = 0
    while True:
        try:
            await asyncio.sleep(env.poll_interval)
            if offset_id := poll_for_messages(offset):
                offset = offset_id
        except EgressErrors as error:
            if isinstance(error, requests.exceptions.ReadTimeout):
                continue
            LOGGER.error(error)
            failed_connections += 1
            if failed_connections > env.max_retries:
                LOGGER.critical("ATTENTION::Couldn't recover from connection error. Restarting current process.")
                delay = failed_connections * env.backoff_factor
                LOGGER.info("Restarting in %d seconds.", delay)
                await asyncio.sleep(delay)  # Simple backoff wait
        except (
            asyncio.CancelledError,
            BotWebhookConflict,
            BotInUse,
            BotTokenInvalid,
            KeyboardInterrupt,
            Exception,
        ) as error:
            if isinstance(error, asyncio.CancelledError):
                LOGGER.info("Shutting down all threads and gracefully terminated.")
            else:
                LOGGER.error(error)
            shutdown_event()
            break
