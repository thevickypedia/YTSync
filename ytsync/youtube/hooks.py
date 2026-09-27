import asyncio
import logging
import pathlib
from typing import Any, Awaitable, Dict, List

from ytsync.modules import config
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
        Returns an awaitable bridged back onto the main event loop if there is a transfer
        to gather, or ``None`` for transient files.

    See Also:
        This hook fires synchronously from yt-dlp, which now runs inside a worker thread via
        ``asyncio.to_thread`` — there is no running loop on that thread, so ``asyncio.create_task``
        cannot be used. ``run_coroutine_threadsafe`` schedules the coroutine onto the main loop
        from any thread; ``wrap_future`` turns the resulting ``concurrent.futures.Future`` into
        something ``asyncio.gather`` can await once control returns to the main loop.
    """
    local_path = pathlib.Path(local_path.strip())
    if local_path.suffix in TRANSIENT_FILES:
        LOGGER.debug("Transient download complete; awaiting final - %s", local_path)
        return None
    LOGGER.info("Ready to transfer: %s", local_path)
    coro = callbacks.transfer_file(local_path, stats)
    future = asyncio.run_coroutine_threadsafe(coro, config.MAIN_EVENT_LOOP)
    return asyncio.wrap_future(future, loop=config.MAIN_EVENT_LOOP)


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
