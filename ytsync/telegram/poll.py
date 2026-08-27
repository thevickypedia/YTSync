import asyncio
import logging

import requests.exceptions

from ytsync.modules import config, exceptions
from ytsync.telegram import bot

LOGGER = logging.getLogger("ytsync")


async def stop_polling() -> None:
    """Stop polling for incoming messages."""
    task = bot.ACTIVE_TASKS.pop("poll", None)
    if task and not task.done():
        LOGGER.info("Stopping long poll")
        task.cancel("polling stopped")
        try:
            await task
        except asyncio.CancelledError:
            pass


def start_polling() -> None:
    """Start polling for incoming messages."""
    task = bot.ACTIVE_TASKS.get("poll")
    if task and not task.done():
        LOGGER.warning("Polling task already running")
        return
    LOGGER.info("Polling for incoming messages...")
    bot.ACTIVE_TASKS["poll"] = asyncio.create_task(run_polling())


async def run_polling():
    """Starts up all the threads and gracefully terminates the processes."""
    offset = 0
    failed_connections = 0
    while True:
        try:
            await asyncio.sleep(config.env.poll_interval)
            if offset_id := await bot.poll_for_messages(offset):
                offset = offset_id
        except exceptions.EgressErrors as error:
            if isinstance(error, requests.exceptions.ReadTimeout):
                continue
            LOGGER.error(error)
            failed_connections += 1
            if failed_connections > config.env.max_retries:
                LOGGER.critical("ATTENTION::Couldn't recover from connection error. Restarting current process.")
                delay = failed_connections * config.env.backoff_factor
                LOGGER.info("Restarting in %d seconds.", delay)
                await asyncio.sleep(delay)  # Simple backoff wait
        except (
            asyncio.CancelledError,
            exceptions.BotWebhookConflict,
            exceptions.BotInUse,
            exceptions.BotTokenInvalid,
            KeyboardInterrupt,
            Exception,
        ) as error:
            if isinstance(error, asyncio.CancelledError):
                LOGGER.info("Terminated due to event cancellation.")
            else:
                LOGGER.exception(error)
            break
