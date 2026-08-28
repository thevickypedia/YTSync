import functools
import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

from ytsync.youtube import callbacks

LOGGER = logging.getLogger("ytsync")


def postprocess_hook(
    local_path: str,
    transfer_pool: ThreadPoolExecutor,
    stats: Dict[str, int],
) -> None:
    """Submit a completed file to the thread pool."""
    local_path = local_path.strip()
    if local_path.endswith(".webm") or local_path.endswith(".part"):
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
    status = data.get("status", "")
    filename = data.get("filename", "unknown")
    if status == "finished":
        stats["downloaded"].append(filename)
        LOGGER.info("Download completed: %s", filename)
    elif status == "error":
        stats["download_failed"].append(filename)
        LOGGER.error("Download failed: %s", filename)
