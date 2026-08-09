import functools
import logging
import os
import time
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from typing import Any, Callable, Dict, List

import yt_dlp
from pydantic import BaseModel

from yt2jf.config import env
from yt2jf.transfer import Rsync

LOGGER = logging.getLogger("uvicorn.default")
process_pool = ProcessPoolExecutor(max_workers=env.max_listeners)
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
    chat_id: int,
    name: str,
):
    """Called when the playlist process finishes."""
    if error := future.exception():
        callback(
            chat_id=chat_id,
            response=f"Transfer failed for {name}\n\n{error}",
        )
        LOGGER.error("Process failed for %s: %s", name, error)
        return

    runtime, successful, failed = future.result()

    callback(
        chat_id=chat_id,
        response=(
            f"Transfer complete: {name}\n\n"
            f"Runtime: {runtime:.2f}s\n"
            f"Successful transfers: {successful}\n"
            f"Failed transfers: {failed}\n"
            f"Total transfers: {successful + failed}"
        ),
    )

    LOGGER.info(
        "Process completed for %s in %.2fs " "(successful=%d, failed=%d)",
        name,
        runtime,
        successful,
        failed,
    )


def queue_download(
    chat_id: int,
    callback: Callable,
    playlist_url: str = None,
    playlist_id: str = None,
) -> str:
    """Queue a playlist download in the process pool.

    Args:
        chat_id: Chat ID to respond to.
        callback: Callback function call to send a notification.
        playlist_url: Full url for the playlist.
        playlist_id: Playlist identifier.

    Returns:
        str:
        Returns the playlist name as a string.
    """
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

    future = process_pool.submit(
        download_playlist,
        playlist_name,
        playlist_url,
    )

    wrapped_callback = functools.partial(
        process_callback,
        callback=callback,
        chat_id=chat_id,
        name=playlist_name,
    )

    future.add_done_callback(wrapped_callback)

    controllers.append(
        Controller(
            name=playlist_name,
            future=future,
        )
    )

    return playlist_name


def transfer_file(filepath: str) -> None:
    """Transfer a completed file."""
    LOGGER.info("Transferring: %s", filepath)
    rsync.run(filepath)
    if env.delete_after_sync:
        LOGGER.info(
            "Transfer complete; deleting: %s",
            filepath,
        )
        os.remove(filepath)


def transfer_callback(
    future: Future,
    filepath: str,
    stats: Dict[str, int],
) -> None:
    """Called when an individual transfer thread completes."""
    try:
        future.result()
    except Exception as exc:
        stats["failed"] += 1
        LOGGER.exception(
            "Transfer failed for %s: %s",
            filepath,
            exc,
        )
    else:
        stats["successful"] += 1
        LOGGER.info(
            "Transfer thread completed: %s",
            filepath,
        )
    LOGGER.debug(
        "Transfers: successful=%d failed=%d",
        stats["successful"],
        stats["failed"],
    )


def postprocess_hook(
    filepath: str,
    transfer_pool: ThreadPoolExecutor,
    stats: Dict[str, int],
) -> None:
    """Submit a completed file to the thread pool."""
    filepath = filepath.strip()
    if filepath.endswith(".webm") or filepath.endswith(".part"):
        LOGGER.debug(
            "Transient download complete; awaiting final - %s",
            filepath,
        )
        return
    LOGGER.info("Ready to transfer: %s", filepath)
    future = transfer_pool.submit(
        transfer_file,
        filepath,
    )
    stats["submitted"] += 1
    future.add_done_callback(
        functools.partial(
            transfer_callback,
            filepath=filepath,
            stats=stats,
        )
    )


def download_playlist(name: str, url: str) -> tuple[float, int, int]:
    """Downloads a playlist.

    Returns:
        tuple:
            runtime,
            successful transfers,
            failed transfers
    """
    destination = env.data_dir.joinpath(name)
    destination.mkdir(exist_ok=True)
    transfer_pool = ThreadPoolExecutor(
        max_workers=env.max_transfers,
        thread_name_prefix=f"transfer-{name}",
    )
    stats: Dict[str, int] = {
        "submitted": 0,
        "successful": 0,
        "failed": 0,
    }
    start = time.time()
    try:
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
        if rsync.is_enabled:

            def hook(filepath: str) -> None:
                postprocess_hook(
                    filepath=filepath,
                    transfer_pool=transfer_pool,
                    stats=stats,
                )

            options["post_hooks"] = [hook]

        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])
    except Exception as exc:
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from None
    finally:
        LOGGER.info(
            "Waiting for %d transfer(s) for %s",
            stats["submitted"],
            name,
        )
        transfer_pool.shutdown(wait=True)
        LOGGER.info(
            "All transfers completed for %s " "(successful=%d, failed=%d)",
            name,
            stats["successful"],
            stats["failed"],
        )
    runtime = time.time() - start
    return (
        runtime,
        stats["successful"],
        stats["failed"],
    )
