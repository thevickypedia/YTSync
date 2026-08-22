import logging
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Callable

LOGGER = logging.getLogger("ytsync")


def _run_with_cooldown(
    function: Callable,
    cooldown: float | int,
    *args,
    **kwargs,
):
    """Runs task sequentially, with a cooldown period between each task."""
    # The first task should run immediately.
    # Subsequent tasks will already be serialized by the single-worker pool,
    # but need to wait for N seconds after the previous task has completed.
    if cooldown:
        time.sleep(cooldown)

    return function(*args, **kwargs)


class Processor:
    """Process object to kick off ProcessPoolExecutor with a single worker and a cooldown period between each execution.

    >>> Processor

    """

    def __init__(self, max_workers: int = 1, cooldown_interval: int = 3):
        """Instantiates the processor object."""
        self.process_pool = ProcessPoolExecutor(max_workers=max_workers)
        self.cooldown_interval = cooldown_interval
        self.total_submissions = 0

    def status(self) -> int | None:
        """Logs the status of process pool executor, and returns the pending count."""
        try:
            pending = len(self.process_pool._pending_work_items)
            queue_count = self.process_pool._queue_count
            LOGGER.info("pending work items: %d, queue count: %d", pending, queue_count)
        except Exception as caught:
            LOGGER.debug(caught)
            LOGGER.warning("Failed to inspect ProcessPoolExecutor internals")
            pending = None
        return pending

    def submit(self, identifier: str, function: Callable, *args, **kwargs):
        """Submits the function for background execution.

        Args:
            identifier: Name for the task.
            function: Function to execute.
        """
        if self.total_submissions == 0:
            cooldown = 0
            LOGGER.info("Submitting %s now", identifier)
        else:
            cooldown = self.cooldown_interval
            LOGGER.info("Submitting %s at: %s (cooldown=%ss)", identifier, time.ctime(time.time() + cooldown), cooldown)
        self.total_submissions += 1
        self.status()
        future = self.process_pool.submit(
            _run_with_cooldown,
            function,
            cooldown,
            *args,
            **kwargs,
        )
        return future

    def shutdown(self, wait: bool = True, cancel_futures: bool = False):
        """Shutdown the entire process pool."""
        LOGGER.info("Shutting down processor with %d in queue", self.process_pool._queue_count)
        self.process_pool.shutdown(wait=wait, cancel_futures=cancel_futures)
