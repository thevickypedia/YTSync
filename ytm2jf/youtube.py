import os
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import Process
from typing import Any, Callable, Dict, List

import yt_dlp
from pydantic import BaseModel
from yt_dlp.utils import DownloadError

from ytm2jf.logger import LOGGER
from ytm2jf.transfer import Rsync

transfer_pool = ThreadPoolExecutor(max_workers=3)
rsync = Rsync()


class Controller(BaseModel):
    """State controller for processes and transfer pools.

    >>> Controller

    """

    name: str
    process: Process
    transfer_pool: ThreadPoolExecutor

    class Config:
        """Allow arbitrary types."""

        arbitrary_types_allowed = True


controllers: List[Controller] = []


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

    options = {
        "format": "bestaudio/best",
        "outtmpl": "%(playlist_title)s/%(title)s.%(ext)s",
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "0",
            }
        ],
    }
    with yt_dlp.YoutubeDL(options) as ydl:
        info = ydl.extract_info(playlist_url, download=False, process=False)

    playlist_name = info.get("title")
    assert playlist_name, "Failed to extract the playlist's title"
    os.makedirs(playlist_name, exist_ok=True)

    downloader = DownloadProcessor(chat_id, callback)
    process = Process(
        target=downloader.download_playlist,
        args=(
            playlist_name,
            playlist_url,
        ),
    )
    process.start()
    controllers.append(
        Controller(name=playlist_name, process=process, transfer_pool=transfer_pool)
    )

    return playlist_name


class DownloadProcessor:
    """Download processor.

    >>> DownloadProcessor

    """

    def __init__(self, chat_id: int, callback: Callable):
        """Instantiates the download processor."""
        self.chat_id = chat_id
        self.callback = callback

    def transfer_file(self, filepath: str) -> None:
        """Initiates the file transfer, once the download has completed."""
        try:
            LOGGER.info(f"Transferring: {filepath}")
            # TODO: Run it with async - and a callback to notify
            rsync.run(filepath)
            LOGGER.info(f"Transfer complete: {filepath}")
            self.callback(
                chat_id=self.chat_id, response=f"Transfer complete: {filepath}"
            )
        except Exception as error:
            LOGGER.error(f"Transfer failed for {filepath}: {error}")
            self.callback(
                chat_id=self.chat_id,
                response=f"Transfer failed for {filepath}\n\n{error}",
            )

    def postprocess_hook(self, data: Dict[str, Any]) -> None:
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
        transfer_pool.submit(self.transfer_file, filepath)

    def download_playlist(self, name: str, url: str) -> str:
        """Downloads the given playlist.

        Args:
            name: Name of the playlist.
            url: URL for the playlist.
        """
        options = {
            "logger": LOGGER,
            "format": "bestaudio/best",
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "0",
                }
            ],
            "outtmpl": os.path.join(name, "%(title)s.%(ext)s"),
        }
        if rsync.is_enabled:
            options["postprocessor_hooks"] = [self.postprocess_hook]
        # TODO: Fail with notifications
        # TODO: Artist, Genre, Year, Title, Name - All music metadata missing
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.download([url])
        except DownloadError as error:
            LOGGER.error(error)
            return
