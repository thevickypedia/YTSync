import asyncio
import logging
from collections.abc import Generator
from typing import Callable, Tuple

import yt_dlp
from pydantic import BaseModel, HttpUrl

from ytsync.modules import config, settings
from ytsync.youtube import youtube

LOGGER = logging.getLogger("ytsync")


class DBSchema(BaseModel):
    """Schema for all DB interactions.

    >>> DBSchema

    """

    url: HttpUrl
    name: str
    schedule: str


def row_to_schema(row: Tuple[str, str, str]) -> DBSchema:
    """Convert a row of tuple into a DBSchema object."""
    fields = DBSchema.model_fields.keys()
    return DBSchema(**dict(zip(fields, row)))


def get() -> Generator[DBSchema]:
    """Get trackers stored in the database."""
    with config.db.connection as connection:
        cursor = connection.cursor()
        data = cursor.execute("SELECT * FROM ytsync").fetchall()
    for row in data:
        yield row_to_schema(row)


def insert(playlist_url: str, return_code: bool = False) -> str | int:
    """Handles tracker for a playlist URL.

    Args:
        playlist_url: URL to sync on schedule.
        return_code: Boolean flag to return HTTP code instead of structured text.

    Returns:
        str:
        Returns the response string for Telegram and HTTP code for API calls.
    """
    with yt_dlp.YoutubeDL() as ydl:
        info = ydl.extract_info(
            playlist_url,
            download=False,
            process=False,
        )
    assert all((info, info.get("title"))), "Failed to get the playlist title"
    title = info["title"]
    # TODO: Schedule should be user-input
    with config.db.connection as connection:
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM ytsync WHERE url = ? LIMIT 1;", (playlist_url,))
        if row := cursor.fetchone():
            tracked = row_to_schema(row)
            if return_code:
                return 409
            return (
                "❌ *Already scheduled*\n\n"
                f"*{tracked.name}* is already scheduled for sync.\n\n"
                f"*URL:* `{tracked.url}`\n\n"
                f"*Schedule:* `{tracked.schedule}`\n\n"
                "Use `/status` to get the schedule index, then "
                "`/delete <index>` to remove it before adding a new schedule."
            )
        cursor.execute(
            "INSERT INTO ytsync (url, name, schedule) VALUES (?,?,?);",
            (playlist_url, title, config.env.default_tracker),
        )
        connection.commit()
    if return_code:
        return 200
    # TODO: Expand schedule to meaningful statement
    return f"✅ *Sync scheduled*\n\n" f"*{title}* will be synced on schedule:\n" f"`{config.env.default_tracker}`"


def sync(idx: int, chat: settings.Chat | None = None, callback: Callable | None = None) -> str:
    """Syncs a tracker (on-demand) by its 1-based status index.

    Args:
        idx: Index to be synced.

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
    LOGGER.info("Executing sync for '%s' with '%s'", tracker.name, tracker.url)
    asyncio.create_task(youtube.queue_download(chat=chat, playlist_url=tracker.url, callback=callback))
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
    with config.db.connection as connection:
        cursor = connection.cursor()
        cursor.execute(
            "DELETE FROM ytsync WHERE url = ?;",
            (tracker.url,),
        )
        connection.commit()
    if return_code:
        return 200
    return (
        "✅ *Tracker deleted*\n\n"
        f"*{tracker.name}* has been removed from the sync schedule.\n\n"
        f"*URL:* `{tracker.url}`\n"
        f"*Schedule:* `{tracker.schedule}`"
    )
