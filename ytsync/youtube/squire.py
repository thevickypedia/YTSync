import logging
import pathlib
from collections.abc import Generator
from dataclasses import dataclass
from typing import Any, Dict, List, Tuple

import yt_dlp
from pydantic import HttpUrl
from yt_dlp.utils import YoutubeDLError

from ytsync.modules import checkpoint, config
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


@dataclass
class PreProcessor:
    """Pre-process the information block."""

    url_file_map: Dict[str, pathlib.Path]
    preflight: checkpoint.PreFlight
    total_files: int | None = None


def get_missing_entries(
    url: HttpUrl,
    ydl: yt_dlp.YoutubeDL,
    info: Dict[str, Any],
    destination: pathlib.Path,
) -> PreProcessor:
    """Get missing entries from a parent URL either in the local directory or remote server.

    Args:
        url: Parent URL.
        ydl: YouTube download object.
        info: Block of entries needed.
        destination: Local path to the destination.

    Returns:
        List[str]:
        Returns a list of all the missing entries.
    """
    preflight = checkpoint.PreFlight()
    entries = info.get("entries")
    if not entries:
        # TODO: This must not skip the exist checker?
        # This is a valid scenario for standalone links, instead of a playlist
        return PreProcessor(url_file_map={str(url): destination}, preflight=preflight)
    url_file_map: Dict[str, pathlib.Path] = {}
    for entry in info["entries"]:
        preflight.total += 1
        if not entry or not entry.get("url"):
            LOGGER.warning("Invalid entry found: %s", entry or "None")
            preflight.error += 1
            continue
        # TODO: Check if it is possible to get the file size
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
            preflight.error += 1
            continue
        url_file_map[entry["url"]] = destination.joinpath(filename)

    if not url_file_map:
        return PreProcessor(url_file_map={str(url): destination}, preflight=preflight)
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
            LOGGER.info("'%s' already exists; skipping...", local_path)
            preflight.available += 1
            continue
        LOGGER.info("'%s' does not exist", local_path)
        url_file_map_copy[url] = local_path
        preflight.unavailable += 1

    LOGGER.info(preflight.model_dump(mode="json"))
    # If there are items marked as unavailable,
    # don't care about error count since they'll likely fail to download for the same reason
    if preflight.unavailable:
        return PreProcessor(url_file_map=url_file_map_copy, preflight=preflight, total_files=len(url_file_map_copy))
    # If there are more than N% of errors, then let's not take a chance - just try and download the entire playlist
    if preflight.error > preflight.total * (config.env.max_error_threshold / 100):
        LOGGER.info(
            "Error count %d EXCEEDS the acceptable threshold of %d pct", preflight.error, config.env.max_error_threshold
        )
        return PreProcessor(url_file_map={str(url): destination}, preflight=preflight)
    # Happy path - no unavailability and error rate is within the acceptable bounds
    LOGGER.info(
        "Error count %d is within the acceptable threshold of %d pct", preflight.error, config.env.max_error_threshold
    )
    return PreProcessor(url_file_map=url_file_map_copy, preflight=preflight, total_files=len(url_file_map_copy))


def get_info(url: HttpUrl) -> Tuple[yt_dlp.YoutubeDL, Dict[str, Any]]:
    """Get info based on the given YT URL.

    Args:
        url: Download URL.

    Returns:
        Tuple[yt_dlp.YoutubeDL, Dict[str, Any]]:
        Returns a tuple of YoutubeDL object, and a dictionary of information block.
    """
    with yt_dlp.YoutubeDL() as ydl:
        info = ydl.extract_info(
            str(url),
            download=False,
            process=False,
        )
    return ydl, info
