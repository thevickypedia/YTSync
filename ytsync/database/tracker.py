import asyncio
import logging
from typing import List, Tuple

import yt_dlp

from ytsync.modules import config
from ytsync.youtube import youtube

LOGGER = logging.getLogger("ytsync")


def get() -> List[Tuple[str, str, str]]:
    """Get trackers stored in the database."""
    # TODO: Create a structured array with dataclasses for all DB interactions including columns and tables
    with config.db.connection as connection:
        cursor = connection.cursor()
        return cursor.execute("SELECT * FROM ytsync").fetchall()


def insert(playlist_url: str, return_code: bool = False) -> str | int:
    """Handles tracker for a playlist URL.

    Args:
        playlist_url: URL to sync on schedule.
        return_bool: Boolean flag to return indicator flag instead of structured text.

    Returns:
        str:
        Returns the response string for Telegram.
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
        row = cursor.fetchone()
        if row is not None:
            url, name, schedule = row
            if return_code:
                return 409
            return (
                "❌ *Already scheduled*\n\n"
                f"*{name}* is already scheduled for sync.\n\n"
                f"*URL:* `{url}`\n\n"
                f"*Schedule:* `{schedule}`\n\n"
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


def sync(idx: int) -> str:
    """Syncs a tracker (on-demand) by its 1-based status index."""
    idx -= 1
    with config.db.connection as connection:
        cursor = connection.cursor()
        trackers = cursor.execute("SELECT url, name, schedule FROM ytsync").fetchall()
        if not trackers:
            return "⚠️ No trackers found!"
        if idx < 0 or idx >= len(trackers):
            return (
                "❌ *Invalid tracker index*\n\n"
                f"Tracker `{idx + 1}` does not exist.\n\n"
                "Use `/status` to see the available tracker indexes."
            )
        url, name, _ = trackers[idx]
        LOGGER.info("Executing sync for '%s' with '%s'", name, url)
        # TODO: Implement alternate notifications or just pass chat into this
        asyncio.create_task(youtube.queue_download(playlist_url=url))
    return f"✅ *Sync queued*\n\n" f"*{name}* will be synced shortly."


def delete(idx: int, return_code: bool = False) -> str:
    """Delete a tracker by its 1-based status index."""
    with config.db.connection as connection:
        cursor = connection.cursor()
        trackers = cursor.execute("SELECT url, name, schedule FROM ytsync").fetchall()
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
        url, name, schedule = trackers[idx]
        cursor.execute(
            "DELETE FROM ytsync WHERE url = ?;",
            (url,),
        )
        connection.commit()
    if return_code:
        return 200
    return (
        "✅ *Tracker deleted*\n\n"
        f"*{name}* has been removed from the sync schedule.\n\n"
        f"*URL:* `{url}`\n"
        f"*Schedule:* `{schedule}`"
    )
