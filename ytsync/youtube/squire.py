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
    """Pre-process the information block.

    >>> PreProcessor

    """

    url_file_map: Dict[str, pathlib.Path]
    preflight: checkpoint.PreFlight
    total_files: int | None = None


def filter_existing(base_url_file_map: Dict[str, pathlib.Path]) -> Dict[str, pathlib.Path]:
    """Filter out the existing files from the given URL-file map.

    Args:
        base_url_file_map: Base key-value map of URL to filepath.

    Returns:
        Dict[str, pathlib.Path]:
        Returns a key-value map of URL to filepath that doesn't exist in the local/remote data directory.
    """
    if transfer.rsync.is_enabled:
        # Check files' presence in remote server
        existing = transfer.rsync.remote_files_exist(list(base_url_file_map.values()))
    else:
        # Check files' presence in local data directory
        existing = {str(file) for file in base_url_file_map.values() if file.exists()}

    # Redundant loop, but it's a necessary evil because of a cleaner remote check
    # Avoid modifying the original dict since python doesn't support dropping values from dict while looping on it
    url_file_map: Dict[str, pathlib.Path] = {}
    for url, local_path in base_url_file_map.items():
        # 'existing' is mapped to 'Set[str]'
        if str(local_path) in existing:
            LOGGER.info("'%s' already exists; skipping...", local_path)
            continue
        LOGGER.info("'%s' does not exist", local_path)
        url_file_map[url] = local_path
    return url_file_map


def generate_file_map(
    ydl: yt_dlp.YoutubeDL,
    entries: List[Dict[str, Any]],
    destination: pathlib.Path,
) -> Dict[str, pathlib.Path]:
    """Generate a file map for the given entries.

    Args:
        ydl: YoutubeDL object.
        entries: List of entries to generate the file map for.
        destination: Destination path.

    Returns:
        Dict[str, pathlib.Path]:
        Returns a key-value map of URL to filepath.
    """
    url_file_map: Dict[str, pathlib.Path] = {}
    for entry in entries:
        if not entry or not entry.get("url"):
            LOGGER.warning("Invalid entry found: %s", entry or "None")
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
            continue
        url_file_map[entry["url"]] = destination.joinpath(filename)
    return url_file_map


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
        PreProcessor:
        Returns a PreProcessor object with the URL-file map and preflight information.
    """
    preflight = checkpoint.PreFlight()
    if entries := info.get("entries", []):
        entries = list(entries)
        preflight.total = len(entries)
        LOGGER.debug("Found %d entries", preflight.total)
    else:
        # No entries found, likely a single video/audio file, no preflight check needed
        LOGGER.debug("No entries found; returning the parent URL as-is")
        return PreProcessor(url_file_map=filter_existing({str(url): destination}), preflight=preflight)

    if base_url_file_map := generate_file_map(ydl, entries, destination):
        # Calculate the number of files for which the filename resolution failed
        preflight.error = len(entries) - len(base_url_file_map)
        LOGGER.debug("Generated file map: %s", base_url_file_map)
    else:
        # Unable to check if there are any child URLs within the parent URL, include base preflight
        preflight.error = len(entries)
        LOGGER.debug("Unable to generate URL file map; returning the parent URL as-is")
        LOGGER.debug(preflight.model_dump(mode="json"))
        return PreProcessor(url_file_map=filter_existing({str(url): destination}), preflight=preflight)

    if url_file_map := filter_existing(base_url_file_map):
        preflight.unavailable = len(url_file_map)
        preflight.available = len(base_url_file_map) - preflight.unavailable
    else:
        LOGGER.debug(preflight.model_dump(mode="json"))
        # No files need to be downloaded, include base preflight
        return PreProcessor(url_file_map={}, preflight=preflight)

    # Likely a playlist file
    LOGGER.info(preflight.model_dump(mode="json"))
    # If there are items marked as unavailable,
    # don't care about error count since they'll likely fail to download for the same reason
    if preflight.unavailable:
        return PreProcessor(url_file_map=url_file_map, preflight=preflight, total_files=len(url_file_map))
    # If there are more than N% of errors, then let's not take a chance - just try and download the entire playlist
    if preflight.error > preflight.total * (config.env.max_error_threshold / 100):
        LOGGER.info(
            "Error count %d EXCEEDS the acceptable threshold of %d pct", preflight.error, config.env.max_error_threshold
        )
        return PreProcessor(url_file_map=filter_existing({str(url): destination}), preflight=preflight)
    # Happy path - no unavailability and error rate is within the acceptable bounds
    LOGGER.info(
        "Error count %d is within the acceptable threshold of %d pct", preflight.error, config.env.max_error_threshold
    )
    return PreProcessor(url_file_map=url_file_map, preflight=preflight, total_files=len(url_file_map))


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
