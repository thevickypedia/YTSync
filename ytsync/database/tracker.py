import asyncio
import logging
from collections.abc import Generator
from typing import Callable, Tuple

from pydantic import BaseModel, HttpUrl

from ytsync.modules import config, settings
from ytsync.youtube import youtube

LOGGER = logging.getLogger("ytsync")


class DBSchema(BaseModel):
    """Schema for all DB interactions.

    >>> DBSchema

    Must follow insertion order: "INSERT INTO ytsync (url, name, schedule) VALUES (?,?,?);"
    """

    url: HttpUrl
    name: str
    schedule: config.AllowedCronSchedule
    index: int


def row_to_schema(index: int, row: Tuple[str, str, str]) -> DBSchema:
    """Convert a row of tuple into a DBSchema object."""
    fields = DBSchema.model_fields.keys()
    wrapped = dict(zip(fields, row))
    wrapped["index"] = index
    wrapped["schedule"] = getattr(config.AllowedCronSchedule, wrapped["schedule"])
    return DBSchema(**wrapped)


def get() -> Generator[DBSchema]:
    """Get trackers stored in the database."""
    with config.db.connection as connection:
        cursor = connection.cursor()
        data = cursor.execute("SELECT * FROM ytsync").fetchall()
    for idx, row in enumerate(data):
        yield row_to_schema(idx, row)


def insert(playlist_url: str, schedule: config.AllowedCronSchedule, return_code: bool = False) -> str | int:
    """Handles tracker for a playlist URL.

    Args:
        playlist_url: URL to sync on schedule.
        schedule: Schedule to follow for tracking the given playlist.
        return_code: Boolean flag to return HTTP code instead of structured text.

    Returns:
        str:
        Returns the response string for Telegram and HTTP code for API calls.
    """
    with config.db.connection as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM ytsync WHERE url = ? LIMIT 1;", (playlist_url,))
        if row := cursor.fetchone():
            tracked = row_to_schema(0, row)
            LOGGER.warning("Schedule updated for %s from %s to %s", tracked.name, tracked.schedule.name, schedule.name)
            title = tracked.name
        else:
            _, yt_info = youtube.get_info(playlist_url)
            assert all((yt_info, yt_info.get("title"))), "Failed to get the playlist title"
            title = yt_info["title"]
        cursor.execute(
            "INSERT OR REPLACE INTO ytsync (url, name, schedule) VALUES (?,?,?);",
            (playlist_url, title, schedule.name),
        )
        connection.commit()
    if return_code:
        return 200
    return f"✅ *Sync scheduled*\n\n" f"*{title}* will be synced {schedule.name.lower()}"


def sync(idx: int, chat: settings.Chat | None = None, callback: Callable | None = None) -> str:
    """Syncs a tracker (on-demand) by its 1-based status index.

    Args:
        idx: Index to be synced.
        chat: Chat object to send a notification as callback.
        callback: Callback function call once the task has completed.

    Returns:
        str:
        Returns the response string for Telegram.
    """
    idx -= 1
    trackers = list(get())
    if not trackers:
        return "⚠️ No trackers found!"
    if idx < 0 or idx >= len(trackers):
        return (
            "❌ *Invalid tracker index*\n\n"
            f"Tracker `{idx + 1}` does not exist.\n\n"
            "Use `/status` to see the available tracker indexes."
        )
    tracker = trackers[idx]
    url = str(tracker.url)
    LOGGER.info("Executing sync for '%s' with '%s'", tracker.name, url)
    asyncio.create_task(youtube.queue_download(chat=chat, playlist_url=url, callback=callback))
    return f"✅ *Sync queued*\n\n" f"*{tracker.name}* will be synced shortly."


def delete(idx: int, return_code: bool = False) -> str | int:
    """Delete a tracker by its 1-based status index.

    Args:
        idx: Index of the list to be cleared.
        return_code: Boolean flag to return HTTP code instead of structured text.

    Returns:
        str:
        Returns the response string for Telegram and HTTP code for API calls.
    """
    trackers = list(get())
    if not trackers:
        if return_code:
            return 404
        return "⚠️ No trackers found!"
    if idx < 0 or idx >= len(trackers):
        if return_code:
            return 400
        return (
            "❌ *Invalid tracker index*\n\n"
            f"Tracker `{idx + 1}` does not exist.\n\n"
            "Use `/status` to see the available tracker indexes."
        )
    tracker = trackers[idx]
    url = str(tracker.url)
    with config.db.connection as connection:
        cursor = connection.cursor()
        cursor.execute(
            "DELETE FROM ytsync WHERE url = ?;",
            (url,),
        )
        connection.commit()
    if return_code:
        return 200
    return (
        "✅ *Tracker deleted*\n\n"
        f"*{tracker.name}* has been removed from the sync schedule.\n\n"
        f"*URL:* `{url}`\n"
        f"*Schedule:* `{tracker.schedule}`"
    )
