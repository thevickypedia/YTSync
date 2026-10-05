import json
import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from ytsync.modules import config
from ytsync.youtube import checkpoint, squire

LOGGER = logging.getLogger("ytsync")
QUEUE_SOURCE = config.env.data_dir / "queue.json"
now_utc = lambda: datetime.now(timezone.utc)  # noqa: E731


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


async def count(profile_name: str) -> int:
    """Count the number of entries in the queue.

    Args:
        profile_name: Takes a profile name as an argument. Use "*" to get all profiles.

    Returns:
        int:
        Get the Queue count of total items.
    """
    async with config.db.connection as connection:
        cursor = connection.cursor()
        if profile_name == "*":
            total = cursor.execute("SELECT COUNT(data) FROM queue").fetchone()[0]
        else:
            total = cursor.execute(
                "SELECT COUNT(data) FROM queue WHERE profile_name = ?",
                (profile_name,),
            ).fetchone()[0]
        return total


async def get(profile_name: str) -> AsyncGenerator[Queue]:
    """Get queues stored in the database.

    Args:
        profile_name: Takes a profile name as an argument. Use "*" to get all profiles.

    Yields:
        Queue:
        Yields a Queue object for each entry in the database.
    """
    async with config.db.connection as connection:
        cursor = connection.cursor()
        if profile_name == "*":
            data = cursor.execute("SELECT data FROM queue").fetchall()
        else:
            data = cursor.execute("SELECT data FROM queue WHERE profile_name = ?", (profile_name,)).fetchall()
    for row in data:
        if row:
            yield Queue(**json.loads(row[0]))


async def insert(profile_name: str, queue: Queue) -> None:
    """Handles tracker for a playlist URL.

    Args:
        profile_name: Takes a profile name as an argument.
        queue: Takes a Queue object as an argument.
    """
    timestamp = datetime.fromisoformat(queue.scheduled_time).timestamp()
    data = queue.model_dump_json()
    async with config.db.connection as connection:
        cursor = connection.cursor()
        cursor.execute(
            "INSERT OR REPLACE INTO queue (profile_name, timestamp, data) VALUES (?,?,?);",
            (
                profile_name,
                timestamp,
                data,
            ),
        )
        connection.commit()


async def delete(profile_name: str, scheduled_time: str) -> bool:
    """Delete a queue entry by its scheduled time.

    Args:
        profile_name: Takes a profile name as an argument.
        scheduled_time: ISO-8601 scheduled time of the queue entry to remove.

    Returns:
        bool:
        Returns True if the entry was deleted, False if no entry was found.
    """
    timestamp = datetime.fromisoformat(scheduled_time).timestamp()
    async with config.db.connection as connection:
        cursor = connection.cursor()
        cursor.execute(
            "SELECT COUNT(data) FROM queue WHERE profile_name = ? AND timestamp = ?",
            (
                profile_name,
                timestamp,
            ),
        )
        if cursor.fetchone()[0] == 0:
            LOGGER.warning(
                "No queue entry found for %s at scheduled_time: %s with timestamp: %s",
                profile_name,
                scheduled_time,
                timestamp,
            )
            return False
        cursor.execute(
            "DELETE FROM queue WHERE profile_name = ? AND timestamp = ?",
            (
                profile_name,
                timestamp,
            ),
        )
        connection.commit()
        return True


async def latest_timestamp() -> int:
    """Get the most recent profile agnostic Queue based on the timestamp in the table.

    Returns:
        int | None:
        Retrieves the most recent checkpoint timestamp if available, otherwise None.
    """
    async with config.db.connection as connection:
        cursor = connection.cursor()
        latest_queue = cursor.execute("SELECT timestamp FROM queue ORDER BY timestamp DESC LIMIT 1").fetchone()
        return int(latest_queue[0])


async def render_scheduled_time(name: str, pending: int, last_ran: int, cp: bool = False) -> datetime:
    """Get the scheduled time for the next queue entry.

    Args:
        name: Name of the file/playlist being submitted.
        pending: Number of pending queue entries.
        last_ran: Timestamp of the last queue entry.
        cp: Boolean indicating if the scheduling is based on a checkpoint (True) or queue (False).

    See Also:
        - This is a reusable function for both queue and checkpoint based scheduling.
        - Queue scheduling is based on the last queue entry in the database table 'queue'.
        - Checkpoint scheduling is based on the last checkpoint timestamp stored in the 'checkpoints' directory.

    Returns:
        datetime:
        Returns the scheduled time for the next queue entry.
    """
    item = "checkpoint" if cp else "queue"
    elapsed = now_utc().timestamp() - last_ran
    if elapsed >= config.env.cooldown_interval:
        # The previous cooldown has completely elapsed.
        scheduled_time = now_utc() + timedelta(seconds=config.env.next_buffer)
        LOGGER.info(
            "Previous %s was scheduled %s ago; cooldown has elapsed. Scheduling %s at %s",
            item,
            str(timedelta(seconds=int(elapsed))),
            name,
            scheduled_time.astimezone(tz=config.env.tz).isoformat(),
        )
    elif elapsed >= 0:
        # The previous cooldown is partially elapsed.
        remaining = config.env.cooldown_interval - elapsed
        scheduled_time = now_utc() + timedelta(seconds=remaining + ((pending + 1) * config.env.next_buffer))
        LOGGER.info(
            "Previous %s was scheduled %s ago; %s of cooldown remains. Scheduling %s at %s",
            item,
            str(timedelta(seconds=int(elapsed))),
            str(timedelta(seconds=int(remaining))),
            name,
            scheduled_time.astimezone(tz=config.env.tz).isoformat(),
        )
    else:
        # The previous scheduled time is still in the future.
        last_timestamp = datetime.fromtimestamp(last_ran, tz=timezone.utc)
        scheduled_time = last_timestamp + timedelta(seconds=config.env.cooldown_interval + config.env.next_buffer)
        LOGGER.info(
            "Previous %s is scheduled for %s; adding cooldown and buffer. Scheduling %s at %s",
            item,
            last_timestamp.astimezone(tz=config.env.tz).isoformat(),
            name,
            scheduled_time.astimezone(tz=config.env.tz).isoformat(),
        )
    return scheduled_time


async def get_scheduled_time_by_checkpoint() -> int | None:
    """Get the scheduled time for the next queue entry based on the last checkpoint.

    Returns:
        int | None:
        Returns the most recent checkpoint timestamp if available, otherwise None.
    """
    most_recent_timestamp = None
    for profile in config.env.profiles:
        async for datestamp, timestamps in checkpoint.ls(profile_name=profile.name):
            if timestamps:
                most_recent_timestamp = max(
                    most_recent_timestamp or 0,
                    max(timestamps),
                )
    return most_recent_timestamp


async def get_scheduled_time(name: str) -> datetime:
    """Get the scheduled time for the next queue entry based on the last checkpoint.

    Args:
        name: Name of the file/playlist being submitted.

    Returns:
        datetime:
        Returns the scheduled time for the next queue entry.
    """
    # All queued items are pending to be executed in the future
    if q_count := await count("*"):
        scheduled_time = await render_scheduled_time(name=name, pending=q_count, last_ran=await latest_timestamp())
    # All checkpoints are past items that were already executed
    elif recent_checkpoint := await get_scheduled_time_by_checkpoint():
        # If no queue entries exist, use the last checkpoint to determine the next scheduled time.
        scheduled_time = await render_scheduled_time(name=name, pending=0, last_ran=recent_checkpoint, cp=True)
    else:
        if config.env.delayed_start:
            scheduled_time = now_utc() + timedelta(seconds=config.env.cooldown_interval + config.env.next_buffer)
            LOGGER.info(
                "Submitting %s at: %s",
                name,
                scheduled_time.astimezone(tz=config.env.tz).isoformat(),
            )
        else:
            scheduled_time = now_utc() + timedelta(seconds=config.env.next_buffer)
    LOGGER.info("Submitting %s at: %s", name, scheduled_time.astimezone(tz=config.env.tz).isoformat())
    return scheduled_time


async def submit(
    profile_name: str,
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
        profile_name: Takes a profile name as an argument.
        name: Name of the task being submitted. Used for logging only.
        checkpoint_stats: Checkpoint statistics associated with the queued task.
        preprocessor_stats: Preprocessor statistics associated with the queued task.
        cron_schedule: Cron schedule, if it is a scheduled run.

    Returns:
        int:
        Number of seconds from now until the newly submitted task is scheduled to run.
    """
    if config.env.download_tester:
        scheduled_time = now_utc() + timedelta(seconds=config.env.next_buffer)
        LOGGER.info(
            "Submitting %s at %s; running in tester mode",
            name,
            scheduled_time.astimezone(tz=config.env.tz).isoformat(),
        )
    else:
        scheduled_time = await get_scheduled_time(name=name)
    cooldown = max(0, (scheduled_time - now_utc()).total_seconds())
    LOGGER.info("Final cooldown for %s: %.2fs", name, cooldown)
    await insert(
        profile_name,
        Queue(
            scheduled_time=scheduled_time.isoformat(),
            checkpoint=checkpoint_stats,
            preprocessor=preprocessor_stats,
            cron_schedule=cron_schedule,
        ),
    )
    return cooldown
