import asyncio
import logging

import requests.exceptions

from ytsync.modules import config, exceptions
from ytsync.telegram import bot, webhook

LOGGER = logging.getLogger("ytsync")


async def restart_loop(after: int) -> None:
    """Restart telegram polling.

    Args:
        after: Delay in seconds.

    See Also:
        - Stops telegram polling immediately.
        - Sleeps for the mentioned delay.
        - Sets the restart_loop flag to true.
    """
    # Stop polling immediately in the main loop
    config.telegram_beat.poll_for_messages = False
    # Sleep for the # of seconds after which the loop should be restarted
    await asyncio.sleep(after)
    # Set restart loop to True, which will re-trigger init with 3s trials
    config.telegram_beat.restart_loop = True


async def terminate(reason: str) -> None:
    """Terminate telegram polling.

    Args:
        reason: Reason for termination.
    """
    LOGGER.info("Terminating telegram polling due to %s", reason)
    config.telegram_beat.poll_for_messages = False
    config.telegram_beat.restart_loop = False


async def executor():
    """Starts up all the threads and gracefully terminates the processes."""
    try:
        offset = await bot.poll_for_messages(config.telegram_beat.offset)
        if offset is not None:
            config.telegram_beat.offset = offset
    except exceptions.BotWebhookConflict as error:
        # At this point, it is be safe to remove the dead webhook
        LOGGER.error(error)
        webhook.delete_webhook()
        await restart_loop(after=1)
    except exceptions.BotInUse as error:
        LOGGER.error(error)
        LOGGER.info("Restarting for webhook to take over...")
        await restart_loop(after=1)
    except exceptions.BotTokenInvalid as error:
        LOGGER.error("ATTENTION: %s", error)
        await terminate(reason=type(error).__name__)
    except exceptions.EgressErrors as error:
        if isinstance(error, requests.exceptions.ReadTimeout):
            return
        LOGGER.error(error)
        config.telegram_beat.failed_connections += 1
        if config.telegram_beat.failed_connections > config.env.max_retries:
            LOGGER.critical("ATTENTION::Couldn't recover from connection error. Restarting current process.")
            delay = config.telegram_beat.failed_connections * config.env.backoff_factor
            LOGGER.info("Restarting in %d seconds.", delay)
            await restart_loop(after=delay)
    except (
        asyncio.CancelledError,
        KeyboardInterrupt,
        Exception,
    ) as error:
        if isinstance(error, asyncio.CancelledError):
            LOGGER.info("Terminated due to event cancellation.")
        else:
            LOGGER.exception(error)
        await terminate(reason=type(error).__name__)
