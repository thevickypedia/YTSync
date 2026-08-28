import logging
import pathlib
from collections.abc import Generator
from typing import Any, Dict, List, Tuple

import yt_dlp
from yt_dlp.utils import YoutubeDLError

from ytsync.modules import config
from ytsync.remote import transfer

LOGGER = logging.getLogger("ytsync")


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
        # This is a valid scenario for individual song files, instead of a playlist
        LOGGER.debug("'info' block does not contain valid 'entries': %s", info)
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
                    pathlib.Path(
                        ydl.prepare_filename(entry, outtmpl=str(destination.joinpath(config.YT_FILENAME_TEMPLATE)))
                    )
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
    if transfer.rsync.is_enabled:
        # Check files' presence in remote server
        existing = transfer.rsync.remote_files_exist(list(url_file_map.values()))
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
