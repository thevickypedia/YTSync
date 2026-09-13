import asyncio
import functools
import logging
import time
from collections.abc import Coroutine
from datetime import datetime, timezone

from ytsync.crontab import expression
from ytsync.database import tracker
from ytsync.modules import checkpoint, config, exceptions
from ytsync.telegram import bot, handler, poll
from ytsync.youtube import callbacks, downloader, queue, youtube

LOGGER = logging.getLogger("ytsync")
LAST_CHECK: datetime | None = None
BG_INTERVAL: int = 5


def callback(task: asyncio.Task) -> None:
    """Callback for background tasks.

    Args:
        task: Takes the async task object as a parameter.
    """
    name, start_time = task.get_name().rsplit("||", maxsplit=1)
    start_time = int(start_time)
    end_time = int(time.time())
    approx_start = datetime.fromtimestamp(start_time).strftime("%a %b %d %H:%M %Y %Z")
    approx_end = datetime.fromtimestamp(end_time).strftime("%a %b %d %H:%M %Y %Z")
    log = not name.startswith("poll_")
    if log:
        LOGGER.info("Task [%s] running since: %s, completed at: [%s]", name, approx_start, approx_end)
    try:
        result = task.result()
        if log:
            LOGGER.info("Background task [%s] completed successfully", name)
            LOGGER.info(result)
    except Exception as error:
        LOGGER.exception(error)
        LOGGER.error("Background task [%s] failed to finish", name)


def create_task(coro: Coroutine, name: str) -> asyncio.Task:
    """Creates a background task with a name.

    Args:
        coro: The coroutine to be executed in the background.
        name: The name of the task.

    Returns:
        asyncio.Task:
        The created asyncio task.
    """
    task = asyncio.create_task(coro, name=f"{name}||{int(time.time())}")
    task.add_done_callback(callback)
    return task


async def run_tracker() -> None:
    """Run all the trackers, per the schedule."""
    for track in tracker.get():
        try:
            cron_expr = expression.CronExpression(track.schedule.value)
        except exceptions.InvalidArgument as error:
            LOGGER.error("Invalid cron expression for '%s': %s", track.name, error)
            tracker.delete(name=track.name, url=str(track.url), chat_id=track.chat_id, raise_for_exception=False)
            continue
        # Since check_trigger() is true for the whole minute, the last_check guard handles the twice-per-minute case
        # schedule.value is used ONLY here, all inbound and outbound requests follow schedule.name for user-friendly
        if cron_expr.check_trigger():
            LOGGER.info("Executing sync for '%s' with '%s'", track.name, track.url)
            # Background task; so no timeout required
            create_task(
                youtube.queue_download(
                    url=track.url,
                    source_system=checkpoint.SourceSystem(scheduled=track.schedule),
                    callback=bot.reply_to,
                    chat_id=track.chat_id,
                    schedule=track.schedule,
                ),
                name=track.name,
            )


async def run_queued(now: datetime) -> None:
    """Run all the queued items per the scheduled time."""
    for q in queue.get():
        scheduled_time = datetime.fromisoformat(q.scheduled_time)
        if scheduled_time.replace(second=0, microsecond=0) != now:
            continue
        task = asyncio.create_task(
            downloader.download(
                checkpoint_stats=q.checkpoint,
                preprocess_stats=q.preprocessor,
            )
        )
        if telegram := q.checkpoint.source_system.telegram:
            chat_id = telegram.id
            message_id = telegram.message_id
        else:
            chat_id = message_id = None
        task.add_done_callback(
            functools.partial(
                callbacks.process_callback,
                name=q.checkpoint.name,
                chat_id=chat_id,
                message_id=message_id,
                schedule=None,
            )
        )


async def single_task() -> None:
    """Executes a single iteration of the main loop.

    Polls for incoming messages, read the database and execute the YouTube sync for the requested URL.
    """
    global LAST_CHECK
    # MARK: Runs every 5s
    if config.telegram_beat.poll_for_messages:
        create_task(poll.executor(), name="poll_executor")
    if config.telegram_beat.restart_loop:
        LOGGER.debug("Restarting loop...")
        # Avoid being called again when init is in progress
        config.telegram_beat.restart_loop = False
        create_task(handler.init(), name="poll_init")
    now = datetime.now(tz=timezone.utc).replace(second=0, microsecond=0)
    if now == LAST_CHECK:
        return
    # MARK: Runs every minute
    LAST_CHECK = now
    LOGGER.debug("Heart beat for background task: %s", now.astimezone(config.env.tz).strftime("%Y-%m-%d %H:%M"))
    await run_tracker()
    await run_queued(now)


async def executor() -> None:
    """Executes in a loop to read the database and execute the YouTube sync for the requested URL."""
    create_task(handler.init(), name="poll_init")
    while True:
        await asyncio.sleep(BG_INTERVAL)
        try:
            await single_task()
        except Exception as error:
            LOGGER.exception(error)
