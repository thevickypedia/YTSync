import functools
import logging
import pathlib
import posixpath
import time
from concurrent.futures import Future
from datetime import datetime, timedelta, timezone
from typing import Callable, List

from pydantic import BaseModel, HttpUrl

from ytsync.modules import checkpoint, config
from ytsync.remote import transfer
from ytsync.youtube import callbacks, downloader, process, squire

LOGGER = logging.getLogger("ytsync")
processor = process.Processor(
    cooldown_interval=config.env.cooldown_interval,
    buffer=config.env.next_buffer,
    delayed_start=config.env.delayed_start,
)


class Controller(BaseModel):
    """State controller for processes and transfer pools.

    >>> Controller

    """

    name: str
    future: Future

    class Config:
        """Allow arbitrary types."""

        arbitrary_types_allowed = True


controllers: List[Controller] = []


async def queue_download(
    playlist_url: str,
    source_system: checkpoint.SourceSystem,
    chat_id: int | None = None,
    message_id: int | None = None,
    callback: Callable | None = None,
    schedule: config.AllowedCronSchedule | None = None,
) -> str | None:
    """Queue a playlist download in the process pool."""
    LOGGER.debug("Playlist URL: %s", playlist_url)
    ydl, info = squire.get_info(playlist_url)
    playlist_name = info.get("title", None) or None
    assert playlist_name and isinstance(playlist_name, str), "Failed to extract the playlist's title"

    destination = config.env.download_dir.joinpath(playlist_name)
    destination.mkdir(exist_ok=True)

    url_file_loc, preflight_stats = squire.get_missing_playlist_entries(ydl, info, destination)
    if url_file_loc is None:
        url_file_loc = {playlist_url: destination}
        total_files = None
    else:
        total_files = len(url_file_loc)
    intended_path = (
        posixpath.join(transfer.rsync.remote_path, playlist_name) if transfer.rsync.is_enabled else destination
    )
    if not url_file_loc:
        assert preflight_stats, "Something went wrong! Neither URLs, nor preflight status were received!"
        if source_system.api:
            return f"{playlist_name!r} with {preflight_stats.total} file(s) is already available at: {intended_path}"
        callback(
            chat_id=chat_id,
            message_id=message_id,
            response="ℹ️ *Already available*\n\n"
            f"*{playlist_name}* with {preflight_stats.total} file(s) is already available at:\n"
            f"`{intended_path}`",
        )
        return None

    checkpoint_stats = checkpoint.Checkpoint(
        source_system=source_system,
        input_url=HttpUrl(playlist_url),
        resolved_urls=list(map(HttpUrl, url_file_loc.keys())),
        initial_destination=destination,
        final_destination=pathlib.Path(intended_path),
        name=playlist_name,
        preflight=preflight_stats,
    )

    future, scheduled_time = processor.submit(
        identifier=playlist_name,
        function=downloader.download_playlist,
        **dict(
            checkpoint_stats=checkpoint_stats,
            name=playlist_name,
            url_file_map=url_file_loc,
            total_files=total_files,
            destination=destination,
        ),
    )

    wrapped_callback = functools.partial(
        callbacks.process_callback,
        name=playlist_name,
        callback=callback,
        chat_id=chat_id,
        message_id=message_id,
        schedule=schedule,
    )

    future.add_done_callback(wrapped_callback)

    controllers.append(
        Controller(
            name=playlist_name,
            future=future,
        )
    )

    scheduled_time = max(0, scheduled_time - time.monotonic())
    if total_files is None:
        parsed_len = " "
    else:
        parsed_len = f" - {total_files} file(s) "
    if scheduled_time == 0:
        if source_system.api:
            txt = f"{playlist_name!r}{parsed_len}has been queued for download."
        else:
            txt = f"✅ *Download queued*\n\n*{playlist_name}*{parsed_len}queued for download."
    else:
        future_utc = datetime.now(timezone.utc) + timedelta(seconds=scheduled_time)
        zoned_time = future_utc.astimezone(config.env.tz)
        t_string = zoned_time.strftime("%a %b %d %H:%M %Y %Z")
        if source_system.api:
            txt = f"{playlist_name!r}{parsed_len}will be queued for download at {t_string!r}"
        else:
            txt = f"✅ *Download queued*\n\n*{playlist_name}*{parsed_len}" f"will be queued for download at {t_string}"

    # Add a text block about callback notification when 'chat_id' is provided
    if chat_id:
        spacer = " " if source_system.api else "\n\n"
        txt += f"{spacer}You will receive a notification to {chat_id!r} when the process completes."

    if source_system.api:
        return txt

    # Skip start notification for scheduled runs to avoid too much noise
    if schedule:
        LOGGER.info(txt)
    else:
        callback(chat_id=chat_id, message_id=message_id, response=txt)
    return None
