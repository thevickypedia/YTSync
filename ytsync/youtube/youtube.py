import logging
import pathlib
import posixpath
import re
from datetime import datetime, timedelta, timezone

from pydantic import HttpUrl

from ytsync.modules import checkpoint, config
from ytsync.remote import transfer
from ytsync.youtube import queue, squire

LOGGER = logging.getLogger("ytsync")


async def queue_download(
    url: HttpUrl,
    source_system: checkpoint.SourceSystem,
    cron_schedule: config.AllowedCronSchedule | None = None,
) -> str:
    """Queue an input url to download per the next available time."""
    LOGGER.debug("Input URL: %s", url)
    ydl, info = squire.get_info(url)
    name = info.get("title", None) or None
    if not name or not isinstance(name, str):
        LOGGER.error("'title' not found in info dict: %s", info)
        raise ValueError("Failed to extract the title from the URL")
    subdir = re.sub(r'[<>:"/\\|?*]', "_", name)
    if source_system.audio_only:
        destination = config.env.audio_dir.joinpath(subdir)
    else:
        destination = config.env.video_dir.joinpath(subdir)
    destination.mkdir(exist_ok=True, parents=True)

    preprocessed = squire.get_missing_entries(url, ydl, info, destination, source_system)
    intended_path = posixpath.join(transfer.rsync.remote_path, subdir) if transfer.rsync.is_enabled else destination
    if not preprocessed.url_file_map:
        if not preprocessed.preflight:
            raise ValueError("Something went wrong! Neither URLs, nor preflight status were received!")
        if source_system.api:
            return f"{name!r} with {preprocessed.preflight.total} file(s) is already available at: {intended_path}"
        return (
            "ℹ️ *Already available*\n\n"
            f"*{name}* with {preprocessed.preflight.total or 1} file(s) is already available at:\n"
            f"`{intended_path}`"
        )

    checkpoint_stats = checkpoint.Checkpoint(
        source_system=source_system,
        input_url=url,
        resolved_urls=list(map(HttpUrl, preprocessed.url_file_map.keys())),
        is_playlist=len(preprocessed.url_file_map) > 1,
        initial_destination=destination,
        final_destination=pathlib.Path(intended_path),
        name=name,
        preflight=preprocessed.preflight,
    )

    cooldown = queue.submit(
        name=name, checkpoint_stats=checkpoint_stats, preprocessor_stats=preprocessed, cron_schedule=cron_schedule
    )

    scheduled_time = datetime.now(timezone.utc) + timedelta(seconds=cooldown)
    if preprocessed.total_files is None:
        parsed_len = " "
    else:
        parsed_len = f" - {preprocessed.total_files} file(s) "
    if cooldown == 0:
        if source_system.api:
            txt = f"{name!r}{parsed_len}has been queued for download."
        else:
            txt = f"✅ *Download queued*\n\n*{name}*{parsed_len}queued for download."
    else:
        zoned_time = scheduled_time.astimezone(config.env.tz)
        t_string = zoned_time.strftime("%a %b %d %H:%M %Y %Z")
        if source_system.api:
            txt = f"{name!r}{parsed_len}will be queued for download at {t_string!r}"
        else:
            txt = f"✅ *Download queued*\n\n*{name}*{parsed_len}" f"will be queued for download at {t_string}"

    # Add a text block about callback notification when 'source_system' is 'telegram'
    if source_system.telegram:
        spacer = " " if source_system.api else "\n\n"
        txt += f"{spacer}You will receive a notification to {source_system.telegram.id!r} when the process completes."

    return txt
