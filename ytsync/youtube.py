import functools
import logging
import os
import time
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from typing import Any, Callable, Dict, List

import yt_dlp
from pydantic import BaseModel

from ytsync.config import env
from ytsync.settings import Chat
from ytsync.transfer import Rsync

LOGGER = logging.getLogger("ytsync")
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
    chat: Chat,
    name: str,
):
    """Called when the playlist process finishes."""
    if error := future.exception():
        callback(
            chat=chat,
            response=f"Transfer failed for {name}\n\n{error}",
        )
        LOGGER.error("Process failed for %s: %s", name, error)
        return

    result = future.result()

    runtime = result["runtime"]
    downloaded = result["downloaded"]
    download_failed = result["download_failed"]
    transferred = result["transferred"]
    transfer_failed = result["transfer_failed"]

    callback(
        chat=chat,
        response=(
            f"Transfer complete: {name}\n\n"
            f"Runtime: {runtime:.2f}s\n\n"
            f"Downloads:\n"
            f"  Successful: {downloaded}\n"
            f"  Failed: {download_failed}\n\n"
            f"Transfers:\n"
            f"  Successful: {transferred}\n"
            f"  Failed: {transfer_failed}"
        ),
    )

    LOGGER.info(
        "Process completed for %s in %.2fs "
        "(downloads: successful=%d, failed=%d; "
        "transfers: successful=%d, failed=%d)",
        name,
        runtime,
        downloaded,
        download_failed,
        transferred,
        transfer_failed,
    )


def queue_download(
    chat: Chat,
    callback: Callable,
    playlist_url: str = None,
    playlist_id: str = None,
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

    future = process_pool.submit(
        download_playlist,
        playlist_name,
        playlist_url,
    )

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
    future.add_done_callback(
        functools.partial(
            transfer_callback,
            filepath=filepath,
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
    url: str,
) -> Dict[str, Any]:
    """Downloads a playlist and returns download/transfer statistics."""
    destination = env.data_dir.joinpath(name)
    destination.mkdir(exist_ok=True)
    transfer_pool = ThreadPoolExecutor(
        max_workers=env.max_transfers,
        thread_name_prefix=f"transfer-{name}",
    )
    stats: Dict[str, int] = {
        "downloaded": 0,
        "download_failed": 0,
        "transferred": 0,
        "transfer_failed": 0,
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
            "progress_hooks": [
                functools.partial(
                    download_progress_hook,
                    stats=stats,
                )
            ],
        }
        if rsync.is_enabled:

            def hook(filepath: str) -> None:
                """Function to create a post process hook to initiate rsync in the background."""
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
