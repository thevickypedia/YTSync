import os
import yt_dlp
from threading import Thread

from yt_dlp.utils import DownloadError
from ytm2jf.logger import LOGGER


def queue_download(playlist_url: str = None, playlist_id: str = None):
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


def download_playlist(name: str, url: str):
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
    # TODO: Fail with notifications
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            ydl.download([url])
    except DownloadError as error:
        LOGGER.error(error)
        return


if __name__ == "__main__":
    import sys

    download_playlist(playlist_id=sys.argv[1])
