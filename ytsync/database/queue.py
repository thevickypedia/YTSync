import asyncio
import json
import logging
import math
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone
from typing import Tuple

from pydantic import BaseModel

from ytsync.modules import config
from ytsync.youtube import checkpoint, squire

LOGGER = logging.getLogger("ytsync")
now_utc = lambda: datetime.now(timezone.utc)  # noqa: E731

# Rate limit state: files reserved in the open window, and when the most recent batch kicks off.
# Held in memory (single worker); rebuilt from the database after a restart.
_lock = asyncio.Lock()
_state: Tuple[int, datetime] | None = None


class Queue(BaseModel):
    """Queue model with checkpoint and preprocessor objects.

    The 'scheduled_time' is stored as an ISO-8601 UTC timestamp, which is also the primary key of the table.

    >>> Queue

    """

    scheduled_time: str
    checkpoint: checkpoint.Checkpoint
    preprocessor: squire.PreProcessor


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
            data = cursor.execute("SELECT data FROM queue ORDER BY timestamp").fetchall()
        else:
            data = cursor.execute(
                "SELECT data FROM queue WHERE profile_name = ? ORDER BY timestamp", (profile_name,)
            ).fetchall()
    for row in data:
        if row:
            yield Queue(**json.loads(row[0]))


async def insert(profile_name: str, queue: Queue) -> None:
    """Insert (or replace) a queue entry.

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
            (profile_name, timestamp, data),
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
        cursor.execute("DELETE FROM queue WHERE profile_name = ? AND timestamp = ?", (profile_name, timestamp))
        deleted = cursor.rowcount > 0
        connection.commit()
    if not deleted:
        LOGGER.warning("No queue entry found for %s at scheduled_time: %s", profile_name, scheduled_time)
    return deleted


async def get_scheduled_time_by_checkpoint() -> int | None:
    """Get the timestamp of the most recent checkpoint across all profiles.

    Returns:
        int | None:
        Returns the most recent checkpoint timestamp if available, otherwise None.
    """
    most_recent_timestamp = None
    for profile in config.env.profiles:
        async for _, timestamps in checkpoint.ls(profile_name=profile.name):
            if timestamps:
                most_recent_timestamp = max(most_recent_timestamp or 0, max(timestamps))
    return most_recent_timestamp


def plan(used: int, last_kick: datetime, files: int, now: datetime) -> Tuple[datetime, int, datetime]:
    """Place a request of 'files' files into the rate limit window.

    A window holds 'max_sequence' files. Once it is full (or the request can't fit in what is left),
    the next request waits for 'cooldown_interval' since the last batch kicked off. A request larger than
    the window starts on its own and is split by the downloader into batches, one cooldown apart.

    Args:
        used: Files already reserved in the open window.
        last_kick: When the most recent batch kicks off (or kicked off).
        files: Number of files in the new request.
        now: Current time.

    Returns:
        Tuple[datetime, int, datetime]:
        Start time of the request, files used in the window after it, and the kick off time of its last batch.
    """
    limit, cooldown = config.env.max_sequence, timedelta(seconds=config.env.cooldown_interval)
    if now >= last_kick + cooldown:
        used = 0  # Cooldown has fully elapsed, so the window starts over
    start = max(now, last_kick)
    if used and used + files > limit:
        start, used = max(now, last_kick + cooldown), 0  # Window is full, wait for the cooldown
    if used + files <= limit:
        return start, used + files, start
    batches = math.ceil(files / limit)  # Only reachable for a request larger than the window
    return start, files - (batches - 1) * limit, start + (batches - 1) * cooldown


async def load_state(now: datetime, include_queue: bool = True) -> Tuple[int, datetime]:
    """Rebuild the rate limit state after a restart, assuming the window is full to be safe.

    Args:
        now: Datetime object for the current time in UTC.
        include_queue: Boolean flag to include the data from queue table.

    Returns:
        Tuple[int, datetime]:
        A tuple of max sequence and the last ran datetime object.
    """
    limit, cooldown = config.env.max_sequence, timedelta(seconds=config.env.cooldown_interval)
    if include_queue:
        async with config.db.connection as connection:
            row = connection.cursor().execute("SELECT data FROM queue ORDER BY timestamp DESC LIMIT 1").fetchone()
        if row:
            tail = Queue(**json.loads(row[0]))
            files = tail.preprocessor.total_files or len(tail.preprocessor.url_file_map)
            batches = max(1, math.ceil(files / limit))
            return limit, datetime.fromisoformat(tail.scheduled_time) + (batches - 1) * cooldown
    if last_checkpoint := await get_scheduled_time_by_checkpoint():
        return limit, datetime.fromtimestamp(last_checkpoint, tz=timezone.utc)
    if config.env.delayed_start:
        return limit, now
    return 0, now


async def replan(grace: int = 60) -> int:
    """Re-plan the pending queue after downtime, so a backlog doesn't run all at once.

    Overdue entries run on the very first tick, regardless of the window they were planned in. When any entry is
    overdue by more than 'grace' seconds, every pending entry is planned again, oldest first, from the current time.
    Call this once at startup, before the executor starts dispatching.

    Args:
        grace: Seconds an entry may be overdue before it is considered a backlog.

    Returns:
        int:
        Number of entries that were re-planned.
    """
    global _state
    if config.env.download_tester:
        return 0
    async with _lock:
        now = now_utc()
        async with config.db.connection as connection:
            cursor = connection.cursor()
            rows = cursor.execute("SELECT profile_name, timestamp, data FROM queue ORDER BY timestamp").fetchall()
        if not rows or rows[0][1] >= (now - timedelta(seconds=grace)).timestamp():
            return 0
        used, last_kick = await load_state(now, include_queue=False)
        taken, planned = set(), []
        for profile_name, _, data in rows:
            item = Queue(**json.loads(data))
            files = max(1, item.preprocessor.total_files or len(item.preprocessor.url_file_map))
            start, used, last_kick = plan(used, last_kick, files, now)
            while start.timestamp() in taken:
                start += timedelta(milliseconds=1)
                last_kick += timedelta(milliseconds=1)
            taken.add(start.timestamp())
            item.scheduled_time = start.isoformat()
            planned.append((profile_name, start.timestamp(), item.model_dump_json()))
        async with config.db.connection as connection:
            cursor = connection.cursor()
            # Single transaction: commits on exit, rolls back on error
            cursor.executemany("DELETE FROM queue WHERE timestamp = ?", [(row[1],) for row in rows])
            cursor.executemany("INSERT INTO queue (profile_name, timestamp, data) VALUES (?,?,?)", planned)
        _state = used, last_kick
        LOGGER.warning(
            "Re-planned %d pending entries; windows now run %s to %s",
            len(planned),
            datetime.fromtimestamp(planned[0][1], tz=timezone.utc).astimezone(config.env.tz).isoformat(),
            datetime.fromtimestamp(planned[-1][1], tz=timezone.utc).astimezone(config.env.tz).isoformat(),
        )
        return len(planned)


async def submit(
    profile_name: str,
    name: str,
    checkpoint_stats: checkpoint.Checkpoint,
    preprocessor_stats: squire.PreProcessor,
) -> int | float:
    """Submit a new queue entry.

    Args:
        profile_name: Takes a profile name as an argument.
        name: Name of the task being submitted. Used for logging only.
        checkpoint_stats: Checkpoint statistics associated with the queued task.
        preprocessor_stats: Preprocessor statistics associated with the queued task.

    Returns:
        int | float:
        Seconds the request has to wait because of the cooldown. Zero when it can start right away.
    """
    global _state
    files = max(1, preprocessor_stats.total_files or len(preprocessor_stats.url_file_map))
    async with _lock:
        now = now_utc()
        wait = 0.0
        if config.env.download_tester:
            start = now
        else:
            used, last_kick = _state or await load_state(now)
            start, used, last_kick = plan(used, last_kick, files, now)
            wait = max(0.0, (start - now).total_seconds())
            # 'timestamp' is the primary key, so entries can never share a scheduled time
            async with config.db.connection as connection:
                cursor = connection.cursor()
                while cursor.execute("SELECT 1 FROM queue WHERE timestamp = ?", (start.timestamp(),)).fetchone():
                    start += timedelta(milliseconds=1)
                    last_kick += timedelta(milliseconds=1)
            _state = used, last_kick
            LOGGER.info("Window: %d/%d file(s) after %s, starts in %.2fs", used, config.env.max_sequence, name, wait)
        await insert(
            profile_name,
            Queue(scheduled_time=start.isoformat(), checkpoint=checkpoint_stats, preprocessor=preprocessor_stats),
        )
    return wait
