import json
import logging
import os
from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from typing import List

from pydantic import BaseModel

from ytsync.modules import checkpoint, config
from ytsync.youtube import squire

LOGGER = logging.getLogger("ytsync")
QUEUE_SOURCE = config.env.data_dir / "queue.json"


class Queue(BaseModel):
    """Queue model with checkpoint and preprocessor objects.

    The 'scheduled_time' is stored as an ISO-8601 UTC timestamp so that the
    queue contains enough information to reconstruct the scheduling state.

    >>> Queue

    """

    scheduled_time: str
    checkpoint: checkpoint.Checkpoint
    preprocessor: squire.PreProcessor


def get() -> Generator[Queue, None, None]:
    """Get all the existing queues.

    Yields:
        Queue:
        Yields the Queue object for each entry in the queue file.
    """
    # TODO: Switch to a DB - each row as 'json_string = json.dumps(row)'
    if not os.path.isfile(QUEUE_SOURCE):
        return
    with open(QUEUE_SOURCE) as file:
        data_list = json.load(file)
    for q in data_list:
        yield Queue(**q)


def put(queues: List[Queue]) -> None:
    """Put queues into the queue file.

    Args:
        queues: List of queues to persist.
    """
    queues_json = [queue.model_dump(mode="json") for queue in queues]
    with open(QUEUE_SOURCE, "w") as file:
        json.dump(queues_json, file, indent=2)


def submit(
    name: str,
    checkpoint_stats: checkpoint.Checkpoint,
    preprocessor_stats: squire.PreProcessor,
) -> int:
    """Submit a new queue entry.

    See Also:
        - | Each submission reserves the next available slot
          | by adding the configured cooldown interval and buffer to the
          | previously scheduled slot.
        - The first submission runs immediately unless delayed_start is enabled.
        - In tester mode, submissions always run immediately.

    Args:
        name: Name of the task being submitted. Used for logging only.
        checkpoint_stats: Checkpoint statistics associated with the queued task.
        preprocessor_stats: Preprocessor statistics associated with the queued task.

    Returns:
        int:
        Number of seconds from now until the newly submitted task is scheduled to run.
    """
    queues = list(get())
    now = datetime.now(timezone.utc)

    if config.env.download_tester:
        scheduled_time = now
        LOGGER.info("Submitting %s now; running in tester mode", name)
    elif not queues:
        if config.env.delayed_start:
            scheduled_time = now + timedelta(seconds=config.env.cooldown_interval)
            LOGGER.info(
                "Submitting %s after %.2fs of delayed start",
                name,
                config.env.cooldown_interval,
            )
        else:
            scheduled_time = now
            LOGGER.info("Submitting %s now", name)
    else:
        last_queue = max(
            queues,
            key=lambda queue: datetime.fromisoformat(queue.scheduled_time),
        )
        last_scheduled_time = datetime.fromisoformat(last_queue.scheduled_time)
        scheduled_time = last_scheduled_time + timedelta(
            seconds=(config.env.cooldown_interval + config.env.next_buffer)
        )
        LOGGER.info(
            "Submitting %s for %s; %.2fs remaining cooldown",
            name,
            scheduled_time.isoformat(),
            max(0, (scheduled_time - now).total_seconds()),
        )
    cooldown = max(0, (scheduled_time - now).total_seconds())
    queues.append(
        Queue(
            scheduled_time=scheduled_time.isoformat(),
            checkpoint=checkpoint_stats,
            preprocessor=preprocessor_stats,
        )
    )
    put(queues)
    return cooldown
