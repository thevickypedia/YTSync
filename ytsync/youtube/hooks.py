import asyncio
import logging
import pathlib
from typing import Any, Awaitable, Dict, List

from ytsync.youtube import callbacks

LOGGER = logging.getLogger("ytsync")
TRANSIENT_FILES = (".webm", ".part")


def postprocess_hook(
    local_path: str,
    stats: Dict[str, List[str]],
) -> Awaitable | None:
    """Create a task to transfer the file.

    Args:
        local_path: Local filepath to transfer.
        stats: Stats to pass to the callback function.

    Returns:
        Awaitable | None:
        Returns an awaitable task if there is an async task to gather.
    """
    local_path = pathlib.Path(local_path.strip())
    if local_path.suffix in TRANSIENT_FILES:
        LOGGER.debug("Transient download complete; awaiting final - %s", local_path)
        return None
    LOGGER.info("Ready to transfer: %s", local_path)
    return asyncio.create_task(callbacks.transfer_file(local_path, stats))


def download_progress_hook(
    data: Dict[str, Any],
    stats: Dict[str, List[str]],
) -> None:
    """Track yt-dlp download completion."""
    status = data.get("status", "unknown")
    filename = data.get("filename", "unknown")
    if filename.endswith(TRANSIENT_FILES):
        LOGGER.debug("Transient download status: %s - %s", filename, status)
        return
    if status == "finished":
        stats["downloaded"].append(filename)
        LOGGER.info("Download completed: %s", filename)
    elif status == "error":
        stats["download_failed"].append(filename)
        LOGGER.error("Download failed: %s", filename)
    else:
        LOGGER.debug("Download status: %s - %s", filename, status)
