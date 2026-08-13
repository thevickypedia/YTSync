import functools
import logging
import os
import pathlib
import time
from collections.abc import Generator
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Tuple

import yt_dlp
from pydantic import BaseModel

from ytsync.modules import config, settings
from ytsync.remote import transfer

LOGGER = logging.getLogger("ytsync")
process_pool = ProcessPoolExecutor(max_workers=1)
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
    """Converts a snake case to pascal cased string."""
    return "".join(word.capitalize() for word in snake_str.split("_"))


def stats_to_markdown(stats: Dict[str, int]) -> Generator[str]:
    """Loops through a dict and updates all keys to be pascal and values to be integers."""
    for k, v in stats.items():
        yield f"**{snake_to_pascal(k)}**: {v}"


def process_callback(
    future: Future, callback: Callable, chat: settings.Chat, name: str, preflight_stats: Dict[str, int]
):
    """Called when the playlist process finishes."""
    if error := future.exception():
        callback(
            chat=chat,
            response=f"Download failed for {name}\n\n{error}",
        )
        LOGGER.error("Process failed for %s: %s", name, error)
        return

    result: Dict[str, int] = future.result()
    runtime = result["runtime"]
    result.pop("runtime")
    response = f"Download completed for {name!r} in {runtime:.2f}s\n\n"
    # No need to check for `rsync.is_enabled` - handled by `preflight_stats` being None
    if preflight_stats:
        p_stats = "\n".join(stats_to_markdown(preflight_stats))
        response += f"**Pre-flight result**:\n{p_stats}\n\n"
    t_stats = "\n".join(stats_to_markdown(result))
    response += f"**Download/Transfer result**:\n{t_stats}\n\n"
    callback(
        chat=chat,
        response=response,
    )


def get_filename(ydl: yt_dlp.YoutubeDL, entry: dict, destination: pathlib.Path) -> str:
    """Extract filename for a potential entry."""
    # noinspection bad-argument-type
    return (
        pathlib.Path(ydl.prepare_filename(entry, outtmpl=str(destination.joinpath(FILENAME_TEMPLATE))))
        .with_suffix(".mp3")
        .name
    )


def get_missing_playlist_entries(
    ydl: yt_dlp.YoutubeDL,
    info: Dict[str, Any],
    destination: pathlib.Path,
    playlist_url: str,
) -> Tuple[List[str], Dict[str, int]]:
    """Get missing entries in a playlist URL when rsync is requested.

    Args:
        ydl: YouTube download object.
        info: Block of entries needed.
        destination: Local path to the destination.
        playlist_url: Playlist URL provided by the user.

    Returns:
        List[str]:
        Returns a list of all the missing entries.
    """
    counter = {"error": 0, "total": 0, "available": 0, "unavailable": 0}
    entries = info.get("entries")
    if not entries:
        LOGGER.warning("'info' block does not contain valid 'entries': %s", info)
        return [playlist_url], counter
    urls = []
    url_file_map = {}
    for entry in info["entries"]:
        counter["total"] += 1
        if not entry or not entry.get("url"):
            LOGGER.warning("Invalid entry found: %s", entry or "None")
            counter["error"] += 1
            continue
        try:
            filename = get_filename(ydl, entry, destination)
        except Exception as error:
            LOGGER.error(error)
            counter["error"] += 1
            continue
        url_file_map[entry["url"]] = destination.joinpath(filename)

    existing = rsync.remote_files_exist(list(url_file_map.values()))
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
        urls.append(url)
        counter["unavailable"] += 1

    LOGGER.info(counter)
    # If there are items marked as unavailable
    # Don't care about error count since they'll likely fail to download for the same reason
    if counter["unavailable"]:
        return urls, counter
    # If there are more than N% of errors, then let's not take a chance - just try and download the entire playlist
    if counter["error"] > counter["total"] * (config.env.max_error_threshold / 100):
        LOGGER.info(
            "Error count %d EXCEEDS the acceptable threshold of %d pct",
            counter["error"],
            config.env.max_error_threshold,
        )
        return [playlist_url], counter
    # Happy path - no unavailability and error rate is within the acceptable bounds
    LOGGER.info(
        "Error count %d is within the acceptable threshold of %d pct", counter["error"], config.env.max_error_threshold
    )
    return urls, counter


async def queue_download(
    chat: settings.Chat,
    callback: Callable,
    playlist_url: str | None = None,
    playlist_id: str | None = None,
) -> str:
    """Queue a playlist download in the process pool."""
    if playlist_url:
        LOGGER.debug("Playlist URL: %s", playlist_url)
    elif playlist_id:
        playlist_url = f"https://music.youtube.com/playlist?list={playlist_id}"
    else:
        raise ValueError("Either 'playlist_url' [OR] 'playlist_id' is required!!")

    with yt_dlp.YoutubeDL() as ydl:
        info = ydl.extract_info(
            playlist_url,
            download=False,
            process=False,
        )

    playlist_name = info.get("title")
    assert playlist_name, "Failed to extract the playlist's title"

    destination = config.env.data_dir.joinpath(playlist_name)
    destination.mkdir(exist_ok=True)

    if rsync.is_enabled and config.env.delete_after_sync:
        urls, preflight_stats = get_missing_playlist_entries(ydl, info, destination, playlist_url)
        if not urls:
            # TODO: All 'os.path.join' needs to consider the destination OperatingSystem - currently assumes POSIX
            return (
                f"{playlist_name!r} with [{preflight_stats['total']}] files "
                f"is already available at {os.path.join(rsync.remote_path, playlist_name)!r}"
            )
    else:
        urls = [playlist_url]
        preflight_stats = None

    future = process_pool.submit(download_playlist, playlist_name, urls, destination)

    wrapped_callback = functools.partial(
        process_callback, callback=callback, chat=chat, name=playlist_name, preflight_stats=preflight_stats
    )

    future.add_done_callback(wrapped_callback)

    controllers.append(
        Controller(
            name=playlist_name,
            future=future,
        )
    )

    return f"Download queued for [{len(urls)}] files in {playlist_name!r}"


def transfer_file(local_path: str) -> None:
    """Transfer a completed file."""
    LOGGER.info("Transferring: %s", local_path)
    rsync.run(source=local_path)
    if config.env.delete_after_sync:
        LOGGER.info(
            "Transfer complete; deleting: %s",
            local_path,
        )
        os.remove(local_path)


def transfer_callback(
    future: Future,
    filepath: str,
    stats: Dict[str, int],
) -> None:
    """Called when an individual transfer thread completes."""
    try:
        future.result()
    except Exception as exc:
        stats["transfer_failed"] += 1
        LOGGER.exception(
            "Transfer failed for %s: %s",
            filepath,
            exc,
        )
    else:
        stats["transferred"] += 1
        LOGGER.info(
            "Transfer completed: %s",
            filepath,
        )
    LOGGER.debug(
        "Transfers: successful=%d failed=%d",
        stats["transferred"],
        stats["transfer_failed"],
    )


def postprocess_hook(
    local_path: str,
    transfer_pool: ThreadPoolExecutor,
    stats: Dict[str, int],
) -> None:
    """Submit a completed file to the thread pool."""
    local_path = local_path.strip()
    if local_path.endswith(".webm") or local_path.endswith(".part"):
        LOGGER.debug(
            "Transient download complete; awaiting final - %s",
            local_path,
        )
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
    stats: Dict[str, int],
) -> None:
    """Track yt-dlp download completion."""
    status = data.get("status", "")
    if status == "finished":
        stats["downloaded"] += 1
        LOGGER.info(
            "Download completed: %s",
            data.get("filename"),
        )
    elif status == "error":
        stats["download_failed"] += 1
        LOGGER.error(
            "Download failed: %s",
            data.get("filename"),
        )


def download_playlist(
    name: str,
    urls: List[str],
    destination: pathlib.Path,
) -> Dict[str, Any]:
    """Downloads a playlist and returns download/transfer statistics."""
    start = time.time()
    stats: Dict[str, int] = {
        "downloaded": 0,
        "download_failed": 0,
    }
    options: Dict[str, Any] = {
        "logger": LOGGER,
        "format": "bestaudio/best",
        "ignoreerrors": True,
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

    transfer_pool = None
    if rsync.is_enabled:
        transfer_pool = ThreadPoolExecutor(
            max_workers=config.env.max_transfers,
            thread_name_prefix=f"transfer-{name}",
        )
        stats.update(
            {
                "transferred": 0,
                "transfer_failed": 0,
            }
        )

        def hook(local_path: str) -> None:
            """Function to create a post process hook to initiate rsync in the background."""
            # noinspection bad-argument-type
            postprocess_hook(
                local_path=local_path,
                transfer_pool=transfer_pool,
                stats=stats,
            )

        options["post_hooks"] = [hook]

    try:
        # noinspection bad-argument-type
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download(urls)
    except Exception as exc:
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from None

    finally:
        if transfer_pool is not None:
            LOGGER.info(
                "Waiting for transfers for %s",
                name,
            )
            transfer_pool.shutdown(wait=True)
            LOGGER.info(
                "All transfers completed for %s " "(successful=%d, failed=%d)",
                name,
                stats["transferred"],
                stats["transfer_failed"],
            )

    return {
        "runtime": time.time() - start,
        **stats,
    }
