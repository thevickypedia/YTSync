import json
import logging
import os
import pathlib
import time
from concurrent.futures import Future
from datetime import datetime
from typing import Any, Callable, Dict, List

from ytsync.modules import checkpoint, config
from ytsync.remote import transfer
from ytsync.youtube import squire

LOGGER = logging.getLogger("ytsync")


def transfer_file(local_path: str) -> None:
    """Transfer a completed file."""
    LOGGER.info("Transferring: %s", local_path)
    transfer.rsync.run(source=local_path)
    LOGGER.info("Successfully synced %s", local_path)
    if transfer.rsync.is_enabled and config.env.delete_after_sync:
        LOGGER.info(
            "Transfer complete; deleting: %s",
            local_path,
        )
        os.remove(local_path)


def transfer_callback(
    future: Future,
    filepath: str,
    stats: Dict[str, List[str]],
) -> None:
    """Called when an individual transfer thread completes."""
    filepath = pathlib.Path(filepath)
    try:
        future.result()
    except Exception as exc:
        stats["transfer_failed"].append(filepath.name)
        LOGGER.exception("Transfer failed for %s: %s", filepath, exc)
    else:
        stats["transferred"].append(filepath.name)
        LOGGER.info("Transfer completed: %s", filepath)
    LOGGER.info("Transfers: successful=%d failed=%d", len(stats["transferred"]), len(stats["transfer_failed"]))


def process_callback(
    future: Future,
    name: str,
    callback: Callable | None = None,
    chat_id: int | None = None,
    message_id: int | None = None,
    schedule: config.AllowedCronSchedule | None = None,
) -> None:
    """Callback function triggered when the process finishes.

    Args:
        future: Future object.
        name: Name assigned to the download content.
        callback: Callback function. This must always be `bot.reply_to` as a callable object.
        chat_id: Telegram Chat ID.
        message_id: Telegram message ID.
        schedule: Cron schedule enum to indicate a scheduled run.
    """
    if schedule:
        schedule = schedule.value.lstrip("@").capitalize()
    if error := future.exception():
        if callback and chat_id:
            # NOTE: callback function must always be 'bot.reply_to' with an explicit 'message_id' - 'None' or otherwise
            if schedule:
                txt = f"❌ *{schedule} download failed for {name!r}*\n\n{error}"
            else:
                txt = f"❌ *Download failed for {name!r}*\n\n{error}"
            callback(chat_id=chat_id, message_id=message_id, response=txt)
        LOGGER.error("Process failed for %s", name)
        return

    result: checkpoint.Checkpoint = future.result()
    if schedule:
        response = (
            f"✅ *{schedule} download completed for {name!r}*\n\n" f"Process completed in `{result.runtime:.2f}s`.\n\n"
        )
    else:
        response = f"✅ *Download completed for {name!r}*\n\n" f"Process completed in `{result.runtime:.2f}s`.\n\n"
    # preflight_status is set to None if checks fail
    if result.preflight:
        p_stats = "\n".join(squire.stats_to_markdown(result.preflight.model_dump(mode="json")))
        response += f"*Pre-flight result:*\n{p_stats}\n\n"
    stats: Dict[str, Any] = {"downloaded": result.downloaded, "download_failed": result.download_failed}
    t_stats = "\n".join(squire.stats_to_markdown(stats))
    response += f"*Download/Transfer result:*\n{t_stats}"
    stats["download_end"] = config.now()
    final_checkpoint = checkpoint.Checkpoint(**{**result.model_dump(mode="json"), **stats}).model_dump(mode="json")
    save_checkpoint(final_checkpoint)
    LOGGER.info(response)
    if callback and chat_id:
        callback(
            chat_id=chat_id,
            message_id=message_id,
            response=response,
        )


def save_checkpoint(final_checkpoint: checkpoint.Checkpoint) -> None:
    """Save a checkpoint to a file.

    Args:
        final_checkpoint: Final checkpoint object.
    """
    LOGGER.debug(final_checkpoint)
    checkpoint_dir = config.checkpoints_dir / datetime.now(config.env.tz).strftime(config.checkpoint_dir_format)
    checkpoint_dir.mkdir(exist_ok=True, parents=True)
    checkpoint_path = checkpoint_dir / f"checkpoint_{int(time.time())}.json"
    with open(checkpoint_path, "w") as file:
        json.dump(final_checkpoint, file, indent=2)
        file.flush()
    LOGGER.info("Checkpoint saved to: %s", checkpoint_path)
