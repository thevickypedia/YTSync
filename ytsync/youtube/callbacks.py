import asyncio
import json
import logging
import os
import pathlib
import time
from datetime import datetime
from typing import Dict, List

from ytsync.modules import checkpoint, config
from ytsync.remote import transfer
from ytsync.telegram import bot
from ytsync.youtube import queue, squire

LOGGER = logging.getLogger("ytsync")


async def transfer_file(local_path: pathlib.Path, stats: Dict[str, List[str]]) -> None:
    """Transfer a completed file."""
    LOGGER.info("Transferring: %s", local_path)
    try:
        await transfer.rsync.run(source=local_path)
    except Exception as exc:
        stats["transfer_failed"].append(local_path.name)
        LOGGER.exception("Transfer failed for %s: %s", local_path, exc)
    else:
        stats["transferred"].append(local_path.name)
        LOGGER.info("Transfer completed: %s", local_path)
        LOGGER.debug("Transfers: successful=%d failed=%d", len(stats["transferred"]), len(stats["transfer_failed"]))
        if config.env.delete_after_sync:
            LOGGER.info(
                "Transfer complete; deleting: %s",
                local_path,
            )
            os.remove(local_path)


def process_callback(
    task: asyncio.Task,
    payload: queue.Queue,
) -> None:
    """Callback function triggered when the process finishes.

    Args:
        task: Asynchronous task.
        payload: Queue object.
    """
    name = payload.checkpoint.name
    if tele := payload.checkpoint.source_system.telegram:
        chat_id = tele.id
        message_id = tele.message_id
    else:
        chat_id = message_id = None
    schedule = payload.cron_schedule

    if schedule:
        schedule = schedule.value.lstrip("@").capitalize()
    if error := task.exception():
        # TODO: Transfer fails might also be caught in this block; handle it separately or just make message agnostic
        if chat_id:
            if schedule:
                txt = f"❌ *{schedule} download failed for {name!r}*\n\n{error}"
            else:
                txt = f"❌ *Download failed for {name!r}*\n\n{error}"
            asyncio.create_task(bot.reply_to(chat_id=chat_id, message_id=message_id, response=txt))
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
        asyncio.create_task(bot.reply_to(chat_id=chat_id, message_id=message_id, response=response))


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
