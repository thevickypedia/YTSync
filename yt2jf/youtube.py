import functools
import logging
import os
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from typing import Any, Callable, Dict, List, NoReturn

import yt_dlp
from pydantic import BaseModel

from yt2jf.config import env
from yt2jf.transfer import Rsync

LOGGER = logging.getLogger("uvicorn.default")
thread_pool = ThreadPoolExecutor(max_workers=env.max_transfers)
process_pool = ProcessPoolExecutor(max_workers=env.max_listeners)
rsync = Rsync()


class Controller(BaseModel):
    """State controller for processes and transfer pools.

    >>> Controller

    """

    name: str
    process_pool: ProcessPoolExecutor
    transfer_pool: ThreadPoolExecutor

    class Config:
        """Allow arbitrary types."""

        arbitrary_types_allowed = True


controllers: List[Controller] = []


def future_callback(future, callback: Callable, chat_id: int, name: str):
    """Process tracker."""
    if error := future.exception():
        callback(
            chat_id=chat_id,
            response=f"Transfer failed for {name}\n\n{error}",
        )
        LOGGER.error(f"Task failed with result: {error}")
    else:
        callback(chat_id=chat_id, response=f"Transfer complete: {name}")
        LOGGER.info(f"Task completed with result: {future.result()}")


def queue_download(
    chat_id: int, callback: Callable, playlist_url: str = None, playlist_id: str = None
) -> str:
    """Queue the download using thread pool.

    Args:
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
        info = ydl.extract_info(playlist_url, download=False, process=False)

    playlist_name = info.get("title")
    assert playlist_name, "Failed to extract the playlist's title"

    future = process_pool.submit(download_playlist, playlist_name, playlist_url)
    wrapped_callback = functools.partial(
        future_callback, callback=callback, chat_id=chat_id, name=playlist_name
    )
    future.add_done_callback(wrapped_callback)

    controllers.append(
        Controller(
            name=playlist_name, process_pool=process_pool, transfer_pool=thread_pool
        )
    )

    return playlist_name


def transfer_file(filepath: str) -> None:
    """Initiates the file transfer, once the download has completed."""
    LOGGER.info(f"Transferring: {filepath}")
    rsync.run(filepath)
    LOGGER.info(f"Transfer complete: {filepath}")


def postprocess_hook(data: Dict[str, Any]) -> None:
    """Checks if file is ready to transfer, and adds it to the threadpool when ready."""
    if data["status"] != "finished":
        return
    info = data["info_dict"]
    filepath = info.get("filepath")
    if not filepath:
        LOGGER.warning("No filepath found even after finishing")
        return
    filepath = filepath.strip()
    if filepath.endswith(".webm"):
        LOGGER.debug("Transient download complete; awaiting final - %s", filepath)
        return
    LOGGER.info(f"Ready to transfer: {filepath}")
    thread_pool.submit(transfer_file, filepath)


def download_playlist(name: str, url: str) -> None | NoReturn:
    """Downloads the given playlist.

    Args:
        name: Name of the playlist.
        url: URL for the playlist.
    """
    destination = env.data_dir.joinpath(name)
    destination.mkdir(exist_ok=True)
    try:
        options = {
            "logger": LOGGER,
            "format": "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "0",
                },
                {"key": "FFmpegMetadata"},
                {"key": "EmbedThumbnail"},
            ],
            "writethumbnail": True,
            "outtmpl": str(destination.joinpath("%(title)s.%(ext)s")),
        }
        if rsync.is_enabled:
            options["postprocessor_hooks"] = [postprocess_hook]
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])
    except Exception as exc:
        # Don't let an un-pickleable exception cross the process boundary.
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from None
