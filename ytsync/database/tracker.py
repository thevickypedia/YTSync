import asyncio
import logging
import time
from collections.abc import Generator
from typing import Callable, List, Tuple

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
    chat_id: int | None = None


def row_to_schema(row: Tuple[str, str, str]) -> DBSchema:
    """Convert a row of tuple into a DBSchema object."""
    fields = DBSchema.model_fields.keys()
    wrapped = dict(zip(fields, row))
    wrapped["schedule"] = getattr(config.AllowedCronSchedule, wrapped["schedule"])
    return DBSchema(**wrapped)


def get() -> Generator[DBSchema]:
    """Get trackers stored in the database."""
    with config.db.connection as connection:
        cursor = connection.cursor()
        data = cursor.execute("SELECT * FROM ytsync").fetchall()
    for row in data:
        yield row_to_schema(row)


def insert(
    playlist_url: str, schedule: config.AllowedCronSchedule, chat_id: int, return_code: bool = False, delay: int = 0
) -> str | int:
    """Handles tracker for a playlist URL.

    Args:
        playlist_url: URL to sync on schedule.
        schedule: Schedule to follow for tracking the given playlist.
        chat_id: Chat ID to notify when the scheduled run has completed/failed.
        return_code: Boolean flag to return HTTP code instead of structured text.
        delay: Number of seconds to sleep, since API supports multiple additions in a single request.

    Returns:
        str:
        Returns the response string for Telegram and HTTP code for API calls.
    """
    with config.db.connection as connection:
        cursor = connection.cursor()
        # Selecting with 'chat_id' prevents cross user access OR data corruption
        # However, selecting with 'chat_id' means the API should also pass the original 'chat_id',
        # when an entry is made via Telegram but updated through the API; 'GET /get-trackers' will give the 'chat_id'
        cursor.execute(
            "SELECT * FROM ytsync WHERE url = ? AND chat_id = ? LIMIT 1;",
            (
                playlist_url,
                chat_id,
            ),
        )
        if row := cursor.fetchone():
            tracked = row_to_schema(row)
            LOGGER.warning("Schedule updated for %s from %s to %s", tracked.name, tracked.schedule.name, schedule.name)
            title = tracked.name
            # Since there is no primary key, 'INSERT OR REPLACE' will NOT prevent duplicates
            cursor.execute("DELETE FROM ytsync WHERE url = ? AND chat_id = ?;")
        else:
            _, yt_info = youtube.get_info(playlist_url)
            assert all((yt_info, yt_info.get("title"))), "Failed to get the playlist title"
            title = yt_info["title"]
            time.sleep(delay)
        cursor.execute(
            "INSERT INTO ytsync (url, name, schedule, chat_id) VALUES (?,?,?,?);",
            (playlist_url, title, schedule.name, chat_id),
        )
        connection.commit()
    if return_code:
        return 200
    return f"✅ *Sync scheduled*\n\n" f"*{title}* will be synced {schedule.name.lower()}"


def stringified_get(trackers: List[DBSchema] | None = None) -> str:
    """Get trackers in a Markdown friendly format."""
    txt = ""
    if trackers is None:
        trackers = list(get())
    if trackers:
        txt += "\n\n*Trackers:*\n"
        for tracked in trackers:
            # icon = random.choice(("🎵", "📁", "📻", "🎶", "🎼", "🔊"))
            txt += f"🎶 *{tracked.name}* — *{tracked.schedule.name.capitalize()}*\n"
    else:
        txt += "\n\n*Trackers:* No trackers found.\n"
        LOGGER.info("No trackers found.")
    return txt


async def sync(
    chat: settings.Chat, name: str | None = None, url: str | None = None, callback: Callable | None = None
) -> None:
    """Syncs a tracker (on-demand) by its 1-based status index.

    Args:
        name: Name of the playlist.
        url: URL for the playlist.
        chat: Chat object to send a notification as callback.
        callback: Callback function call once the task has completed.

    Returns:
        str:
        Returns the response string for Telegram.
    """
    trackers = list(get())
    if name and (tracker := [tracker for tracker in trackers if tracker.name == name]):
        if len(tracker) > 1:
            callback(chat, f"⚠️ *Warning*\n\n{len(tracker)} playlists found with the same name, please specify the URL")
            return
        tracker = tracker[0]
        url = str(tracker.url)
        LOGGER.info("Executing sync for '%s' with '%s'", tracker.name, url)
        await asyncio.wait_for(
            youtube.queue_download(chat_id=chat.id, message_id=chat.message_id, playlist_url=url, callback=callback),
            timeout=config.env.response_timeout,
        )
    elif url and (tracker := [tracker for tracker in trackers if str(tracker.url).rstrip("/") == url.rstrip("/")]):
        # NOTE: This should never happen since insertion deletes and adds a new entry if URL and chat_id matches
        assert len(tracker) > 1, "Multiple trackers found with the same URL, please reach out to the Administrator."
        tracker = tracker[0]
        LOGGER.info("Executing sync for '%s' with '%s'", tracker.name, url)
        await asyncio.wait_for(
            youtube.queue_download(chat_id=chat.id, message_id=chat.message_id, playlist_url=url, callback=callback),
            timeout=config.env.response_timeout,
        )
    elif trackers:
        callback(chat, f"❌ *Error*\n\nInvalid tracker received: {name or url!r}{stringified_get(trackers)}")
    else:
        callback(chat, "⚠️ *Warning*\n\nNo trackers found on the server.")


def delete(
    name: str | None = None,
    url: str | None = None,
    chat_id: int = 0,
    return_code: bool = False,
    trackers: List[DBSchema] | None = None,
) -> str | int:
    """Delete a tracker by its 1-based status index.

    Args:
        name: Name of the playlist.
        url: URL for the playlist.
        chat_id: Telegram chat ID.
        return_code: Boolean flag to return HTTP code instead of structured text.
        trackers: API supports multiple deletion at once, hence the API function gathers all trackers before looping.

    Returns:
        str:
        Returns the response string for Telegram and HTTP code for API calls.
    """
    if trackers is None:
        trackers = list(get())
    if name and (tracker := [tracker for tracker in trackers if tracker.name == name]):
        if len(tracker) > 1:
            return f"⚠️ *Warning*\n\n{len(tracker)} playlists found with the same name, please specify the URL"
    elif url and (tracker := [tracker for tracker in trackers if str(tracker.url).rstrip("/") == url.rstrip("/")]):
        # NOTE: This should never happen since insertion deletes and adds a new entry if URL and chat_id matches
        assert len(tracker) > 1, "Multiple trackers found with the same URL, please reach out to the Administrator."
    elif trackers:
        if return_code:
            return 400
        return f"❌ *Error*\n\nInvalid tracker received: {name or url!r}{stringified_get(trackers)}"
    else:
        if return_code:
            return 404
        return "⚠️ No trackers found!"
    tracker = tracker[0]
    url = str(tracker.url)
    with config.db.connection as connection:
        cursor = connection.cursor()
        # Using 'chat_id' condition prevents cross user access OR data corruption
        # However, deleting with 'chat_id' means the API should also pass the original 'chat_id',
        # when an entry is made via Telegram but deleted through the API; 'GET /get-trackers' will give the 'chat_id'
        cursor.execute(
            "DELETE FROM ytsync WHERE url = ? AND chat_id = ?;",
            (
                url,
                chat_id,
            ),
        )
        connection.commit()
    if return_code:
        return 200
    return (
        "✅ *Tracker deleted*\n\n"
        f"*{tracker.name}* has been removed from the sync schedule.\n\n"
        f"*URL:* `{url}`\n"
        f"*Schedule:* {tracker.schedule.name.capitalize()}"
    )
