import functools
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

from ytsync.youtube import callbacks

LOGGER = logging.getLogger("ytsync")
TRANSIENT_FILES = (".webm", ".part")


def postprocess_hook(
    local_path: str,
    transfer_pool: ThreadPoolExecutor,
    stats: Dict[str, List[str]],
) -> None:
    """Submit a completed file to the thread pool."""
    local_path = local_path.strip()
    if local_path.endswith(TRANSIENT_FILES):
        LOGGER.debug("Transient download complete; awaiting final - %s", local_path)
        return
    LOGGER.info("Ready to transfer: %s", local_path)
    future = transfer_pool.submit(callbacks.transfer_file, local_path)
    future.add_done_callback(
        functools.partial(
            callbacks.transfer_callback,
            filepath=local_path,
            stats=stats,
        )
    )


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
