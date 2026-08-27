import asyncio
import logging
import time
from datetime import datetime

from ytsync.crontab import expression
from ytsync.database import tracker
from ytsync.telegram import bot
from ytsync.youtube import youtube

LOGGER = logging.getLogger("ytsync")
LAST_CHECK: datetime | None = None


def shutdown_event() -> None:
    """Shuts down all the threads and gracefully terminates the processes."""
    youtube.processor.shutdown()
    for controller in youtube.controllers:
        LOGGER.info("Shutting down controller for: %s", controller.name)
        try:
            result = controller.future.result()
        except Exception as exc:
            LOGGER.error("Controller failed for %s: %s", controller.name, exc)
            LOGGER.exception(exc)
        else:
            LOGGER.info("Controller completed for %s: %s", controller.name, result)


def callback(task: asyncio.Task) -> None:
    """Callback for background tasks.

    Args:
        task: Takes the async task object as a parameter.
    """
    name, start_time = task.get_name().rsplit("||", maxsplit=1)
    start_time = int(start_time)
    end_time = int(time.time() - start_time)
    approx_start = datetime.fromtimestamp(start_time).strftime("%a %b %d %H:%M %Y %Z")
    approx_end = datetime.fromtimestamp(end_time).strftime("%a %b %d %H:%M %Y %Z")
    LOGGER.info("Task [%s] running since: %s, completed at: [%s]", name, approx_start, approx_end)
    try:
        result = task.result()
        LOGGER.info("Background task [%s] completed successfully", name)
        LOGGER.info(result)
    except Exception as error:
        LOGGER.exception(error)
        LOGGER.error("Background task [%s] failed to finish", name)


async def executor() -> None:
    """Executes in a loop to read the database and execute the YouTube sync for the requested URL."""
    global LAST_CHECK
    while True:
        await asyncio.sleep(30)
        now = datetime.now().replace(second=0, microsecond=0)
        if now == LAST_CHECK:
            continue
        LAST_CHECK = now
        for track in tracker.get():
            # Since check_trigger() is true for the whole minute, the last_check guard handles the twice-per-minute case
            # schedule.value is used ONLY here, all inbound and outbound requests follow schedule.name for user-friendly
            if expression.CronExpression(track.schedule.value).check_trigger():
                url = str(track.url)
                LOGGER.info("Executing sync for '%s' with '%s'", track.name, url)
                # Background task; so no timeout required
                task = asyncio.create_task(
                    youtube.queue_download(
                        playlist_url=url, callback=bot.reply_to, chat_id=track.chat_id, schedule=track.schedule
                    )
                )
                task.set_name(f"{track.name}||{int(time.time())}")
                task.add_done_callback(callback)
