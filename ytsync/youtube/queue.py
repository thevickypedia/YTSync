import json
import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from ytsync.modules import checkpoint, config
from ytsync.youtube import squire

LOGGER = logging.getLogger("ytsync")
QUEUE_SOURCE = config.env.data_dir / "queue.json"


class Queue(BaseModel):
    """Queue model with checkpoint and preprocessor objects.

    The 'scheduled_time' is stored as an ISO-8601 UTC timestamp so that the
    queue contains enough information to reconstruct the scheduling state.

    >>> Queue

    """

    scheduled_time: str
    checkpoint: checkpoint.Checkpoint
    preprocessor: squire.PreProcessor
    cron_schedule: config.AllowedCronSchedule | None


class QueueCount(BaseModel):
    """Queue model with checkpoint and preprocessor objects.

    >>> QueueCount

    """

    total: int
    pending: int


async def count() -> QueueCount:
    """Count the number of entries in the queue.

    Returns:
        QueueCount:
        Get the Queue count of total and pending items.
    """
    async with config.db.connection as connection:
        cursor = connection.cursor()
        total = cursor.execute("SELECT COUNT(data) FROM queue").fetchone()[0]
        now = datetime.now(tz=timezone.utc).timestamp()
        pending = cursor.execute("SELECT COUNT(data) FROM queue WHERE timestamp >= ?", (now,)).fetchone()[0]
        return QueueCount(total=total, pending=pending)


async def get(include_past: bool = False) -> AsyncGenerator[Queue]:
    """Get queues stored in the database.

    Yields:
        Queue:
        Yields a Queue object for each entry in the database.
    """
    async with config.db.connection as connection:
        cursor = connection.cursor()
        if include_past:
            data = cursor.execute("SELECT data FROM queue").fetchall()
        else:
            now = datetime.now(tz=timezone.utc).timestamp()
            data = cursor.execute("SELECT data FROM queue WHERE timestamp >= ?", (now,)).fetchall()
    for row in data:
        if row:
            yield Queue(**json.loads(row[0]))


async def insert(queue: Queue) -> None:
    """Handles tracker for a playlist URL.

    Args:
        queue: Takes a Queue object as an argument.
    """
    timestamp = datetime.fromisoformat(queue.scheduled_time).timestamp()
    data = queue.model_dump_json()
    async with config.db.connection as connection:
        cursor = connection.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO queue (timestamp, data) VALUES (?,?);",
            (
                timestamp,
                data,
            ),
        )
        connection.commit()


async def delete(scheduled_time: str) -> None:
    """Delete a queue entry by its scheduled time.

    Args:
        scheduled_time: ISO-8601 scheduled time of the queue entry to remove.
    """
    timestamp = datetime.fromisoformat(scheduled_time).timestamp()
    async with config.db.connection as connection:
        cursor = connection.cursor()
        cursor.execute("DELETE FROM queue WHERE timestamp = ?", (timestamp,))
        connection.commit()


async def latest_timestamp() -> Queue:
    """Get the latest Queue based on the timestamp in the table.

    Returns:
        Queue:
        Retrieves a Queue object for the latest timestamp.
    """
    async with config.db.connection as connection:
        cursor = connection.cursor()
        latest_data = cursor.execute("SELECT data FROM queue ORDER BY timestamp DESC LIMIT 1").fetchone()[0]
        return Queue(**json.loads(latest_data))


async def submit(
    name: str,
    checkpoint_stats: checkpoint.Checkpoint,
    preprocessor_stats: squire.PreProcessor,
    cron_schedule: config.AllowedCronSchedule | None = None,
) -> int | float:
    """Submit a new queue entry.

    See Also:
        - | Each submission reserves the next available slot
          | by adding the configured cooldown interval and buffer to the
          | previously scheduled slot.
        - The first submission runs immediately unless delayed_start is enabled.
        - In tester mode, submissions always run immediately.

    Args:
        name: Name of the task being submitted. Used for logging only.
        checkpoint_stats: Checkpoint statistics associated with the queued task.
        preprocessor_stats: Preprocessor statistics associated with the queued task.
        cron_schedule: Cron schedule, if it is a scheduled run.

    Returns:
        int:
        Number of seconds from now until the newly submitted task is scheduled to run.
    """
    q_count = await count()
    now = datetime.now(timezone.utc)

    if config.env.download_tester:
        scheduled_time = now + timedelta(seconds=config.env.next_buffer)
        LOGGER.info(
            "Submitting %s at %s; running in tester mode",
            name,
            scheduled_time.astimezone(tz=config.env.tz).isoformat(),
        )
    elif not q_count.total:
        if config.env.delayed_start:
            scheduled_time = now + timedelta(seconds=config.env.cooldown_interval + config.env.next_buffer)
            LOGGER.info(
                "Submitting %s at: %s",
                name,
                scheduled_time.astimezone(tz=config.env.tz).isoformat(),
            )
        else:
            scheduled_time = now + timedelta(seconds=config.env.next_buffer)
            LOGGER.info("Submitting %s at: %s", name, scheduled_time.astimezone(tz=config.env.tz).isoformat())
    else:
        last_queue = await latest_timestamp()
        last_scheduled_time = datetime.fromisoformat(last_queue.scheduled_time)
        elapsed = (now - last_scheduled_time).total_seconds()
        if elapsed >= config.env.cooldown_interval:
            # The previous cooldown has completely elapsed.
            scheduled_time = now + timedelta(seconds=config.env.next_buffer)
            LOGGER.info(
                "Previous queue was scheduled %s ago; cooldown has elapsed. " "Scheduling %s at %s",
                str(timedelta(seconds=int(elapsed))),
                name,
                scheduled_time.astimezone(tz=config.env.tz).isoformat(),
            )
        elif elapsed >= 0:
            # The previous cooldown is partially elapsed.
            remaining = config.env.cooldown_interval - elapsed
            scheduled_time = now + timedelta(seconds=remaining + ((q_count.pending + 1) * config.env.next_buffer))
            LOGGER.info(
                "Previous queue was scheduled %s ago; %s of cooldown remains. " "Scheduling %s at %s",
                str(timedelta(seconds=int(elapsed))),
                str(timedelta(seconds=int(remaining))),
                name,
                scheduled_time.astimezone(tz=config.env.tz).isoformat(),
            )
        else:
            # The previous scheduled time is still in the future.
            scheduled_time = last_scheduled_time + timedelta(
                seconds=config.env.cooldown_interval + config.env.next_buffer
            )
            LOGGER.info(
                "Previous queue is scheduled for %s; adding cooldown and buffer. " "Scheduling %s at %s",
                last_scheduled_time.astimezone(tz=config.env.tz).isoformat(),
                name,
                scheduled_time.astimezone(tz=config.env.tz).isoformat(),
            )
        LOGGER.info(
            "Submitting %s at: %s",
            name,
            scheduled_time.astimezone(tz=config.env.tz).isoformat(),
        )
    cooldown = max(0, (scheduled_time - now).total_seconds())
    LOGGER.info("Final cooldown for %s: %.2fs", name, cooldown)
    await insert(
        Queue(
            scheduled_time=scheduled_time.isoformat(),
            checkpoint=checkpoint_stats,
            preprocessor=preprocessor_stats,
            cron_schedule=cron_schedule,
        )
    )
    return cooldown
