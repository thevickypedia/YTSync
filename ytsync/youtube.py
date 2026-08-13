import functools
import logging
import os
import pathlib
import time
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from typing import Any, Callable, Dict, List

import yt_dlp
from pydantic import BaseModel

from ytsync.config import env
from ytsync.settings import Chat
from ytsync.transfer import Rsync

LOGGER = logging.getLogger("ytsync")
process_pool = ProcessPoolExecutor(max_workers=1)
rsync = Rsync()


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


def process_callback(
    future: Future,
    callback: Callable,
    chat: Chat,
    name: str,
):
    """Called when the playlist process finishes."""
    if error := future.exception():
        callback(
            chat=chat,
            response=f"Download failed for {name}\n\n{error}",
        )
        LOGGER.error("Process failed for %s: %s", name, error)
        return

    result = future.result()

    runtime = result["runtime"]
    downloaded = result["downloaded"]
    download_failed = result["download_failed"]

    response = (
        f"Download complete: {name}\n\n"
        f"Runtime: {runtime:.2f}s\n"
        f"Successful downloads: {downloaded}\n"
        f"Failed downloads: {download_failed}"
    )

    if "transferred" in result:
        response += (
            f"\n\n" f"Transfers:\n" f"  Successful: {result['transferred']}\n" f"  Failed: {result['transfer_failed']}"
        )

    callback(
        chat=chat,
        response=response,
    )


def get_filename(ydl: yt_dlp.YoutubeDL, entry: dict, destination: pathlib.Path) -> str:
    """Extract filename for a potential entry."""
    # TODO: Set 'outtmpl' an env var (if validation is available in yt_dlp) [OR] top-level var - hardcoded
    return (
        pathlib.Path(ydl.prepare_filename(entry, outtmpl=str(destination.joinpath("%(title)s.%(ext)s"))))
        .with_suffix(".mp3")
        .name
    )


def get_missing_playlist_entries(
    ydl: yt_dlp.YoutubeDL,
    info: Dict[str, Any],
    destination: pathlib.Path,
    playlist_url: str,
) -> List[str]:
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
    # TODO: Make this configurable
    max_failure_threshold = 30

    LOGGER.info("Info: %s", info)
    entries = info.get("entries")
    LOGGER.info("Entries: %s", info.get("entries"))
    if not entries:
        LOGGER.warning("'info' block does not contain valid 'entries': %s", info)
        print(f"'info' block does not contain valid 'entries': {info}")
        return [playlist_url]
    urls = []
    counter = {"error": 0, "total": 0, "available": 0, "unavailable": 0}
    for entry in info["entries"]:
        counter["total"] += 1
        if not entry or not entry.get("url"):
            LOGGER.warning("Invalid entry found: %s", entry or "None")
            print(f"Invalid entry found: {entry or 'None'}")
            counter["error"] += 1
            continue
        try:
            filename = get_filename(ydl, entry, destination)
        except Exception as error:
            print(error)
            LOGGER.error(error)
            counter["error"] += 1
            continue
        LOGGER.info("Processed filename: %s", filename)
        print(f"Processed filename: {filename}")
        local_path = destination.joinpath(filename)
        # TODO: Individual checks will start a new shell - this is EXPENSIVE
        #   Either do threads or check all files in one SSH session
        if rsync.remote_file_exists(local_path):
            LOGGER.info(
                "'%s' already exists on the remote server; skipping...",
                local_path,
            )
            print(f"{local_path!r} already exists on the remote server; skipping...")
            counter["available"] += 1
            continue
        print(f"{local_path} does not exist on the remote server")
        LOGGER.info(
            "'%s' does not exist on the remote server",
            local_path,
        )
        urls.append(entry["url"])
        counter["unavailable"] += 1

    LOGGER.info(counter)
    # If there are items marked as unavailable
    # Don't care about error count since they'll likely fail to download for the same reason
    if counter["unavailable"]:
        return urls
    # If there are more than N% of errors, then let's not take a chance - just try and download the entire playlist
    if counter["error"] > counter["total"] * (max_failure_threshold / 100):
        LOGGER.info(
            "Error count %d EXCEEDS the acceptable threshold of %d pct", counter["error"], max_failure_threshold
        )
        return [playlist_url]
    # Happy path - no unavailability and error rate is within the acceptable bounds
    LOGGER.info("Error count %d is within the acceptable threshold of %d pct", counter["error"], max_failure_threshold)
    return urls


async def queue_download(
    chat: Chat,
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

    destination = env.data_dir.joinpath(playlist_name)
    destination.mkdir(exist_ok=True)

    if rsync.is_enabled and env.delete_after_sync:
        urls = get_missing_playlist_entries(ydl, info, destination, playlist_url)
        if not urls:
            # TODO: All 'os.path.join' needs to consider the destination OperatingSystem - currently assumes POSIX
            return f"{playlist_name!r} is already available at {os.path.join(env.data_dir, playlist_name)}"
    else:
        urls = [playlist_url]

    future = process_pool.submit(download_playlist, playlist_name, urls, destination)

    wrapped_callback = functools.partial(
        process_callback,
        callback=callback,
        chat=chat,
        name=playlist_name,
    )

    future.add_done_callback(wrapped_callback)

    controllers.append(
        Controller(
            name=playlist_name,
            future=future,
        )
    )

    return f"Download queued for {playlist_name!r}"


def transfer_file(local_path: str) -> None:
    """Transfer a completed file."""
    LOGGER.info("Transferring: %s", local_path)
    rsync.run(source=local_path)
    if env.delete_after_sync:
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
    if data.get("status") == "finished":
        stats["downloaded"] += 1

        LOGGER.info(
            "Download completed: %s",
            data.get("filename"),
        )


def download_playlist(
    name: str,
    urls: List[str],
    destination: pathlib.Path,
) -> Dict[str, Any]:
    """Downloads a playlist and returns download/transfer statistics."""
    start = time.time()
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
        "outtmpl": str(destination.joinpath("%(title)s.%(ext)s")),
    }

    stats: Dict[str, int] = {
        "downloaded": 0,
        "download_failed": 0,
    }

    transfer_pool = None
    if rsync.is_enabled:
        transfer_pool = ThreadPoolExecutor(
            max_workers=env.max_transfers,
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
        options["progress_hooks"] = [
            functools.partial(
                download_progress_hook,
                stats=stats,
            )
        ]

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
