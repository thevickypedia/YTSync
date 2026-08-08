import os
from concurrent.futures import ThreadPoolExecutor
from threading import Thread
from typing import Any, Dict

import yt_dlp
from yt_dlp.utils import DownloadError

from ytm2jf.logger import LOGGER
from ytm2jf.transfer import Rsync

transfer_pool = ThreadPoolExecutor(max_workers=3)
rsync = Rsync()


def queue_download(playlist_url: str = None, playlist_id: str = None) -> str:
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

    # TODO: Async start task instead of threads
    thread = Thread(
        target=download_playlist,
        args=(
            playlist_name,
            playlist_url,
        ),
    )
    thread.start()

    return playlist_name


def transfer_file(filepath: str) -> None:
    """Initiates the file transfer, once the download has completed."""
    try:
        LOGGER.info(f"Transferring: {filepath}")
        # TODO: Run it with async - and a callback to notify
        rsync.run(filepath)
        LOGGER.info(f"Transfer complete: {filepath}")
    except Exception as error:
        LOGGER.error(f"Transfer failed for {filepath}: {error}")


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
    transfer_pool.submit(transfer_file, filepath)
    # TODO: During shutdown
    # transfer_pool.shutdown(wait=True)


def download_playlist(name: str, url: str) -> str:
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
        options["postprocessor_hooks"] = [postprocess_hook]
    # TODO: Fail with notifications
    # TODO: Artist, Genre, Year, Title, Name - All music metadata missing
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])
    except DownloadError as error:
        LOGGER.error(error)
        return
