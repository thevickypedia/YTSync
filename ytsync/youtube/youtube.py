import functools
import logging
import os
import pathlib
import posixpath
import time
from collections.abc import Generator
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Tuple

import yt_dlp
from pydantic import BaseModel
from yt_dlp.utils import DownloadError, YoutubeDLError

from ytsync.modules import config
from ytsync.remote import transfer
from ytsync.youtube import process

LOGGER = logging.getLogger("ytsync")
processor = process.Processor(
    cooldown_interval=config.env.cooldown_interval,
    buffer=config.env.next_buffer,
    delayed_start=config.env.delayed_start,
)
rsync = transfer.Rsync()
FILENAME_TEMPLATE = "%(title)s.%(ext)s"


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


def snake_to_pascal(snake_str: str) -> str:
    """Convert a snake_case string to PascalCase."""
    return "".join(word.capitalize() for word in snake_str.split("_"))


def stats_to_markdown(stats: Dict[str, int | List[str]]) -> Generator[str]:
    """Format statistics as Telegram Markdown."""
    for key, value in stats.items():
        if isinstance(value, int):
            yield f"*{snake_to_pascal(key)}*: {value}"
        elif isinstance(value, list):
            joined = "\n".join(f"• {item}" for item in value)
            yield f"*{snake_to_pascal(key)}*:\n{joined}\n"


def process_callback(
    future: Future,
    name: str,
    preflight_stats: Dict[str, int],
    callback: Callable | None = None,
    chat_id: int | None = None,
    message_id: int | None = None,
    schedule: config.AllowedCronSchedule | None = None,
) -> None:
    """Callback function triggered when the playlist process finishes.

    Args:
        future: Future object.
        name: Playlist name.
        preflight_stats: Preflight status dict.
        callback: Callback function. This must always be `bot.reply_to` as a callable object.
        chat_id: Telegram Chat ID.
        message_id: Telegram message ID.
        schedule: Cron schedule enum to indicate a scheduled run.
    """
    if schedule:
        schedule = schedule.value.lstrip("@").capitalize()
    if error := future.exception():
        if callback and chat_id:
            # NOTE: callback function must always be 'bot.reply_to' with an explicit 'message_id' - 'None' or otherwise
            if schedule:
                txt = f"❌ *{schedule} download failed for {name!r}*\n\n{error}"
            else:
                txt = f"❌ *Download failed for {name!r}*\n\n{error}"
            callback(chat_id=chat_id, message_id=message_id, response=txt)
        LOGGER.error("Process failed for %s", name)
        return

    result = future.result()
    runtime = result.pop("runtime")
    if schedule:
        response = f"✅ *{schedule} download completed for {name!r}*\n\n" f"Process completed in `{runtime:.2f}s`.\n\n"
    else:
        response = f"✅ *Download completed for {name!r}*\n\n" f"Process completed in `{runtime:.2f}s`.\n\n"
    # preflight_status is set to None if checks fail
    if preflight_stats:
        p_stats = "\n".join(stats_to_markdown(preflight_stats))
        response += f"*Pre-flight result:*\n{p_stats}\n\n"
    t_stats = "\n".join(stats_to_markdown(result))
    response += f"*Download/Transfer result:*\n{t_stats}"
    LOGGER.info(response)
    if callback and chat_id:
        callback(
            chat_id=chat_id,
            message_id=message_id,
            response=response,
        )


def get_missing_playlist_entries(
    ydl: yt_dlp.YoutubeDL,
    info: Dict[str, Any],
    destination: pathlib.Path,
) -> Tuple[Dict[str, pathlib.Path] | None, Dict[str, int] | None]:
    """Get missing entries in a playlist URL when rsync is requested.

    Args:
        ydl: YouTube download object.
        info: Block of entries needed.
        destination: Local path to the destination.

    Returns:
        List[str]:
        Returns a list of all the missing entries.
    """
    counter = {"error": 0, "total": 0, "available": 0, "unavailable": 0}
    entries = info.get("entries")
    if not entries:
        LOGGER.warning("'info' block does not contain valid 'entries': %s", info)
        return None, None
    url_file_map: Dict[str, pathlib.Path] = {}
    for entry in info["entries"]:
        counter["total"] += 1
        if not entry or not entry.get("url"):
            LOGGER.warning("Invalid entry found: %s", entry or "None")
            counter["error"] += 1
            continue
        try:
            with ydl:
                # noinspection bad-argument-type
                filename = (
                    pathlib.Path(ydl.prepare_filename(entry, outtmpl=str(destination.joinpath(FILENAME_TEMPLATE))))
                    .with_suffix(".mp3")
                    .name
                )
        except YoutubeDLError as error:
            LOGGER.exception(error)
            counter["error"] += 1
            continue
        url_file_map[entry["url"]] = destination.joinpath(filename)

    if not url_file_map:
        return None, None
    if rsync.is_enabled:
        # Check files' presence in remote server
        existing = rsync.remote_files_exist(list(url_file_map.values()))
    else:
        # Check files' presence in local data directory
        existing = {file for file in url_file_map.values() if file.exists()}

    # Redundant loop, but it's a necessary evil because of a cleaner remote check
    # Avoid modifying the original dict since python doesn't support dropping values from dict while looping on it
    url_file_map_copy: Dict[str, pathlib.Path] = {}
    for url, local_path in url_file_map.items():
        if local_path in existing:
            LOGGER.info(
                "'%s' already exists on the remote server; skipping...",
                local_path,
            )
            counter["available"] += 1
            continue
        LOGGER.info(
            "'%s' does not exist on the remote server",
            local_path,
        )
        url_file_map_copy[url] = local_path
        counter["unavailable"] += 1

    LOGGER.info(counter)
    # If there are items marked as unavailable,
    # don't care about error count since they'll likely fail to download for the same reason
    if counter["unavailable"]:
        return url_file_map_copy, counter
    # If there are more than N% of errors, then let's not take a chance - just try and download the entire playlist
    if counter["error"] > counter["total"] * (config.env.max_error_threshold / 100):
        LOGGER.info(
            "Error count %d EXCEEDS the acceptable threshold of %d pct",
            counter["error"],
            config.env.max_error_threshold,
        )
        return None, counter
    # Happy path - no unavailability and error rate is within the acceptable bounds
    LOGGER.info(
        "Error count %d is within the acceptable threshold of %d pct", counter["error"], config.env.max_error_threshold
    )
    return url_file_map_copy, counter


def get_info(playlist_url: str) -> Tuple[yt_dlp.YoutubeDL, Dict[str, Any]]:
    """Get info based on the playlist URL.

    Args:
        playlist_url: Playlist URL.

    Returns:
        Tuple[yt_dlp.YoutubeDL, Dict[str, Any]]:
        Returns a tuple of YoutubeDL object, and a dictionary of information block.
    """
    with yt_dlp.YoutubeDL() as ydl:
        info = ydl.extract_info(
            playlist_url,
            download=False,
            process=False,
        )
    return ydl, info


async def queue_download(
    chat_id: int | None = None,
    message_id: int | None = None,
    callback: Callable | None = None,
    playlist_url: str | None = None,
    playlist_id: str | None = None,
    raw_text: bool = False,
    schedule: config.AllowedCronSchedule | None = None,
) -> str | None:
    """Queue a playlist download in the process pool."""
    if playlist_url:
        LOGGER.debug("Playlist URL: %s", playlist_url)
    elif playlist_id:
        playlist_url = config.PLAYLIST_URL.format(playlist_id=playlist_id)
    else:
        raise ValueError("Either 'playlist_url' [OR] 'playlist_id' is required!!")

    ydl, info = get_info(playlist_url)
    playlist_name = info.get("title", None) or None
    assert playlist_name and isinstance(playlist_name, str), "Failed to extract the playlist's title"

    destination = config.env.download_dir.joinpath(playlist_name)
    destination.mkdir(exist_ok=True)

    url_file_loc, preflight_stats = get_missing_playlist_entries(ydl, info, destination)
    if url_file_loc is None:
        url_file_loc = {playlist_url: destination}
        total_files = None
    else:
        total_files = len(url_file_loc)
    if not url_file_loc:
        assert preflight_stats, "Something went wrong! Neither URLs, nor preflight status were received!"
        intended_path = posixpath.join(rsync.remote_path, playlist_name) if rsync.is_enabled else destination
        if raw_text:
            return f"{playlist_name!r} with {preflight_stats['total']} file(s) is already available at: {intended_path}"
        callback(
            chat_id=chat_id,
            message_id=message_id,
            response="ℹ️ *Already available*\n\n"
            f"*{playlist_name}* with {preflight_stats['total']} file(s) is already available at:\n"
            f"`{intended_path}`",
        )
        return None

    future, scheduled_time = processor.submit(
        identifier=playlist_name,
        function=download_playlist,
        **dict(name=playlist_name, url_file_map=url_file_loc, total_files=total_files, destination=destination),
    )

    wrapped_callback = functools.partial(
        process_callback,
        name=playlist_name,
        preflight_stats=preflight_stats,
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
        if raw_text:
            txt = f"{playlist_name!r}{parsed_len}has been queued for download."
        else:
            txt = f"✅ *Download queued*\n\n*{playlist_name}*{parsed_len}queued for download."
    else:
        future_utc = datetime.now(timezone.utc) + timedelta(seconds=scheduled_time)
        zoned_time = future_utc.astimezone(config.env.tz)
        t_string = zoned_time.strftime("%a %b %d %H:%M %Y %Z")
        if raw_text:
            txt = f"{playlist_name!r}{parsed_len}will be queued for download at {t_string!r}"
        else:
            txt = f"✅ *Download queued*\n\n*{playlist_name}*{parsed_len}" f"will be queued for download at {t_string}"

    # Add a text block about callback notification when 'chat_id' is provided
    if chat_id:
        spacer = " " if raw_text else "\n\n"
        txt += f"{spacer}You will receive a notification to {chat_id!r} when the process completes."

    if raw_text:
        return txt

    # Skip start notification for scheduled runs to avoid too much noise
    if schedule:
        LOGGER.info(txt)
    else:
        callback(chat_id=chat_id, message_id=message_id, response=txt)
    return None


def transfer_file(local_path: str) -> None:
    """Transfer a completed file."""
    LOGGER.info("Transferring: %s", local_path)
    rsync.run(source=local_path)
    LOGGER.info("Successfully synced %s", local_path)
    if rsync.is_enabled and config.env.delete_after_sync:
        LOGGER.info(
            "Transfer complete; deleting: %s",
            local_path,
        )
        os.remove(local_path)


def transfer_callback(
    future: Future,
    filepath: str,
    stats: Dict[str, List[str]],
) -> None:
    """Called when an individual transfer thread completes."""
    filepath = pathlib.Path(filepath)
    try:
        future.result()
    except Exception as exc:
        stats["transfer_failed"].append(filepath.name)
        LOGGER.exception("Transfer failed for %s: %s", filepath, exc)
    else:
        stats["transferred"].append(filepath.name)
        LOGGER.info("Transfer completed: %s", filepath)
    LOGGER.info("Transfers: successful=%d failed=%d", len(stats["transferred"]), len(stats["transfer_failed"]))


def postprocess_hook(
    local_path: str,
    transfer_pool: ThreadPoolExecutor,
    stats: Dict[str, int],
) -> None:
    """Submit a completed file to the thread pool."""
    local_path = local_path.strip()
    if local_path.endswith(".webm") or local_path.endswith(".part"):
        LOGGER.debug("Transient download complete; awaiting final - %s", local_path)
        return
    LOGGER.info("Ready to transfer: %s", local_path)
    future = transfer_pool.submit(transfer_file, local_path)
    future.add_done_callback(
        functools.partial(
            transfer_callback,
            filepath=local_path,
            stats=stats,
        )
    )


def download_progress_hook(
    data: Dict[str, Any],
    stats: Dict[str, List[str]],
) -> None:
    """Track yt-dlp download completion."""
    status = data.get("status", "")
    filename = data.get("filename", "unknown")
    if status == "finished":
        stats["downloaded"].append(filename)
        LOGGER.info("Download completed: %s", filename)
    elif status == "error":
        stats["download_failed"].append(filename)
        LOGGER.error("Download failed: %s", filename)


def download_playlist(
    name: str,
    url_file_map: Dict[str, pathlib.Path],
    total_files: int | None,
    destination: pathlib.Path,
) -> Dict[str, float | int | List[str]]:
    """Downloads a playlist and returns download/transfer statistics."""
    start = time.time()
    stats: Dict[str, List[str]] = {
        "downloaded": [],
        "download_failed": [],
    }
    options: Dict[str, Any] = {
        "logger": LOGGER,
        "quiet": True,
        "format": "bestaudio/best",
        "ignoreerrors": False,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "0",
            },
            {
                "key": "FFmpegMetadata",
            },
            {
                "key": "EmbedThumbnail",
            },
        ],
        "writethumbnail": True,
        "outtmpl": str(destination.joinpath(FILENAME_TEMPLATE)),
        "progress_hooks": [
            functools.partial(
                download_progress_hook,
                stats=stats,
            )
        ],
    }
    if config.env.cookie_file:
        options["cookiefile"] = str(config.env.cookie_file)
    if config.env.source_address:
        options["source_address"] = str(config.env.source_address)
    if config.env.proxy_url:
        options["proxy"] = str(config.env.proxy_url)

    transfer_pool = None
    if rsync.is_enabled:
        transfer_pool = ThreadPoolExecutor(
            max_workers=config.env.max_transfers,
            thread_name_prefix=f"transfer-{name}",
        )
        stats.update(
            {
                "transferred": [],
                "transfer_failed": [],
            }
        )

        def hook(local_path: str) -> None:
            """Function to create a post-process hook to initiate rsync in the background."""
            # noinspection bad-argument-type
            postprocess_hook(
                local_path=local_path,
                transfer_pool=transfer_pool,
                stats=stats,
            )

        options["post_hooks"] = [hook]

    # noinspection bad-argument-type
    with yt_dlp.YoutubeDL(options) as ydl:
        for url, filepath in url_file_map.items():
            try:
                # yt_dlp is single threaded, but it will fail or skip based on 'ignoreerrors' flag
                # This monotonic loop is to properly capture individual errors and attach custom handlers
                ydl.download([url])
            except DownloadError as error:
                LOGGER.exception(error)
                stats["download_failed"].append(filepath.name)

    if len(stats["download_failed"]) == len(url_file_map):
        if total_files is None:
            raise RuntimeError(f"Failed to download {name!r}")
        else:
            joined = "\n".join(f"• {item}" for item in stats["download_failed"])
            raise RuntimeError(f"{len(url_file_map)} download(s) failed for {name!r}\n{joined}")

    if transfer_pool:
        LOGGER.info(
            "Waiting for transfers for %s",
            name,
        )
        transfer_pool.shutdown(wait=True)
        LOGGER.info(
            "All transfers completed for %s " "(successful=%d, failed=%d)",
            name,
            len(stats["transferred"]),
            len(stats["transfer_failed"]),
        )
        rsync.create_playlist(name)
    else:
        create_local_playlist(destination)

    return {
        "runtime": time.time() - start,
        **stats,
    }


def create_local_playlist(destination: pathlib.Path) -> None:
    """Create a .m3u file on the local machine."""
    filepath = destination.joinpath(f"{destination.name}.m3u")
    if files := [file for file in os.listdir(destination) if file.endswith(".mp3")]:
        with open(filepath, "w") as playlist_file:
            playlist_file.write("\n".join(files) + "\n")
        return
    LOGGER.warning(f"No eligible files found in {destination}")
