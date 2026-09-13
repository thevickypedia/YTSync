import asyncio
import json
import logging
import os
import pathlib
import time
from concurrent.futures import Future
from datetime import datetime
from typing import Dict, List

from ytsync.modules import checkpoint, config
from ytsync.remote import transfer
from ytsync.telegram import bot
from ytsync.youtube import squire

LOGGER = logging.getLogger("ytsync")


def transfer_file(local_path: pathlib.Path) -> None:
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
    filepath: pathlib.Path,
    stats: Dict[str, List[str]],
) -> None:
    """Called when an individual transfer thread completes."""
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
    task: asyncio.Task,
    name: str,
    chat_id: int | None = None,
    message_id: int | None = None,
    schedule: config.AllowedCronSchedule | None = None,
) -> None:
    """Callback function triggered when the process finishes.

    Args:
        task: Asynchronous task.
        name: Name assigned to the download content.
        chat_id: Telegram Chat ID.
        message_id: Telegram message ID.
        schedule: Cron schedule enum to indicate a scheduled run.
    """
    if schedule:
        schedule = schedule.value.lstrip("@").capitalize()
    if error := task.exception():
        if chat_id:
            # NOTE: callback function must always be 'bot.reply_to' with an explicit 'message_id' - 'None' or otherwise
            if schedule:
                txt = f"❌ *{schedule} download failed for {name!r}*\n\n{error}"
            else:
                txt = f"❌ *Download failed for {name!r}*\n\n{error}"
            bot.reply_to(chat_id=chat_id, message_id=message_id, response=txt)
        LOGGER.error("Process failed for %s", name)
        return

    result: checkpoint.Checkpoint = task.result()
    if schedule:
        response = (
            f"✅ *{schedule} download completed for {result.name!r}*\n\n"
            f"Process completed in `{result.runtime:.2f}s`."
        )
    else:
        response = f"✅ *Download completed for {result.name!r}*\n\n" f"Process completed in `{result.runtime:.2f}s`."
    if result.is_playlist:
        # preflight only applies for playlists; and set it 0s as default if there is an error
        if any(
            (result.preflight.total, result.preflight.error, result.preflight.available, result.preflight.unavailable)
        ):
            p_stats = "\n".join(squire.stats_to_markdown(result.preflight.model_dump(mode="json")))
            response += f"\n\n*Pre-flight result:*\n{p_stats}"
        stats_msg = f"Downloaded: {len(result.downloaded)} / {result.preflight.total}"
        if result.download_failed:
            joined = "\n".join(f"• {item}" for item in result.download_failed)
            stats_msg += "\n\nDownload Failed: " + f"\n{joined}"
        response += f"\n\n*Download/Transfer result:*\n{stats_msg}"
    if transfer.rsync.is_enabled:
        total = result.transferred + result.transfer_failed
        response += (
            f"\n\n{len(result.transferred)} / {len(total)} transferred to "
            f"{transfer.rsync.remote_host}:{transfer.rsync.remote_path}"
        )
        if result.transfer_failed:
            joined = "\n".join(f"• {item}" for item in result.transfer_failed)
            response += "\n\nTransfer Failed: " + f"\n{joined}"
    result.download_end = config.now()
    final_checkpoint = result.model_dump(mode="json")
    save_checkpoint(final_checkpoint)
    LOGGER.info(response)
    if chat_id:
        bot.reply_to(
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
