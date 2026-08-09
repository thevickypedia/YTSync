import asyncio
import logging

import requests.exceptions

from yt2jf.bot import poll_for_messages
from yt2jf.config import env
from yt2jf.exceptions import BotInUse, BotTokenInvalid, BotWebhookConflict, EgressErrors
from yt2jf.youtube import controllers

LOGGER = logging.getLogger("uvicorn.default")


def shutdown_event():
    """Shuts down all the threads and gracefully terminates the processes."""
    for controller in controllers:
        LOGGER.info("Shutting down controller for: %s", controller.name)
        controller.transfer_pool.shutdown(wait=True)
        controller.process_pool.shutdown(wait=True)


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
                LOGGER.critical(
                    "ATTENTION::Couldn't recover from connection error. Restarting current process."
                )
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
