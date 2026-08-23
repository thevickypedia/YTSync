import functools
import logging
import threading
import time
from concurrent.futures import Future, ProcessPoolExecutor
from typing import Callable, Tuple

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

    def __init__(self, cooldown_interval: int = 3):
        """Instantiates the processor object."""
        self.process_pool = ProcessPoolExecutor(max_workers=1)
        self.cooldown_interval = cooldown_interval
        self.total_submissions = 0

        # Monotonic timestamp of the last completed task.
        self.last_completion_time: float | None = None

        # Protects submission/completion state if submit() can be called concurrently
        self._lock = threading.Lock()

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

    def tracker(self, _: Future, name: str) -> None:
        """Track when a task actually completes."""
        with self._lock:
            self.last_completion_time = time.monotonic()
        LOGGER.info("Task '%s' has completed at %.3f", name, self.last_completion_time)

    def submit(self, identifier: str, function: Callable, *args, **kwargs) -> Tuple[Future, int | float]:
        """Submits the function for background execution.

        Each task waits for the remaining cooldown period since the previous task's completion.

        Args:
            identifier: Name for the task.
            function: Function to execute.

        Returns:
            Tuple[Future, int]:
            Returns a tuple with a future object and cooldown period the task will wait.
        """
        with self._lock:
            now = time.monotonic()

            # No submissions were made and last completion is None - so true first task
            if self.last_completion_time is None and self.total_submissions == 0:
                # First task runs immediately
                cooldown = 0
                LOGGER.info("Submitting %s now", identifier)
            # There are submission(s) but last completion is None - ONE task is still running
            elif self.last_completion_time is None:
                # Number of submissions times the cool down interval
                # Stagger queued tasks so they remain serialized by cooldown
                # A task is currently running and no completion has been observed yet
                # Since no completion is observed, there is no accurate way to determine next cooldown
                cooldown = self.cooldown_interval * self.total_submissions
            # There are submission(s) and last completion is recorded - calculate cooldown based on it
            else:
                elapsed = now - self.last_completion_time
                cooldown = max(0, self.cooldown_interval - elapsed)
                if cooldown:
                    LOGGER.info("Submitting %s with %.2fs remaining cooldown", identifier, cooldown)
                else:
                    LOGGER.info("Submitting %s now (%.2fs since last completion)", identifier, elapsed)
            self.total_submissions += 1
            self.status()
            future = self.process_pool.submit(
                _run_with_cooldown,
                function,
                cooldown,
                *args,
                **kwargs,
            )
            future.add_done_callback(functools.partial(self.tracker, name=identifier))
            return future, cooldown

    def shutdown(self, wait: bool = True, cancel_futures: bool = False):
        """Shutdown the entire process pool."""
        LOGGER.info("Shutting down processor with %d in queue", self.process_pool._queue_count)
        self.process_pool.shutdown(wait=wait, cancel_futures=cancel_futures)
