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
    # but need to wait for the calculated cooldown before running.
    if cooldown:
        time.sleep(cooldown)

    return function(*args, **kwargs)


class Processor:
    """Kicks off ProcessPoolExecutor with a single worker and a cooldown period between each execution.

    >>> Processor

    """

    def __init__(self, cooldown_interval: int = 3, buffer: int = 60):
        """Instantiates the processor object."""
        self.process_pool = ProcessPoolExecutor(max_workers=1)
        self.cooldown_interval = cooldown_interval
        self.buffer = buffer
        self.total_submissions = 0

        # Monotonic timestamp of the last task that actually completed
        # This is updated only by tracker()
        self.last_completion_time: float | None = None

        # Monotonic timestamp representing when the next submitted task
        # is scheduled to be available.
        #
        # Unlike last_completion_time, this is a scheduling value and may
        # be in the future when tasks are queued faster than they complete.
        self.next_available_time: float | None = None

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

        Each task waits for the remaining scheduled cooldown period.

        The first task runs immediately. Each subsequent submission reserves
        the next available slot by advancing it by cooldown_interval + buffer.

        Args:
            identifier: Name for the task.
            function: Function to execute.

        Returns:
            Tuple[Future, int | float]:
            Returns a future object and the cooldown period the task will wait.
        """
        with self._lock:
            now = time.monotonic()

            # No submissions were made - this is the true first task
            if self.total_submissions == 0:
                # First task runs immediately
                cooldown = 0
                # Reserve the next available slot.
                self.next_available_time = now + self.cooldown_interval + self.buffer
                LOGGER.info("Submitting %s now", identifier)

            # There are already submissions. Use the scheduled next available
            # time rather than last_completion_time.
            #
            # This is important because the actual completion time is unknown
            # while a task is still running. We therefore reserve slots based
            # on the previous scheduled slot adding a buffer to it.
            else:
                cooldown = max(0, self.next_available_time - now)
                LOGGER.info("Submitting %s with %.2fs remaining cooldown", identifier, cooldown)
                # Reserve the next slot as well.
                #
                # Assuming task execution itself takes zero time for scheduling
                # Every submission moves the next slot by the cooldown interval + safety buffer
                self.next_available_time += self.cooldown_interval + self.buffer
            self.total_submissions += 1
            self.status()
            future = self.process_pool.submit(
                _run_with_cooldown,
                function,
                cooldown,
                *args,
                **kwargs,
            )
            future.add_done_callback(
                functools.partial(
                    self.tracker,
                    name=identifier,
                )
            )
            return future, cooldown

    def shutdown(self, wait: bool = True, cancel_futures: bool = False):
        """Shutdown the entire process pool."""
        LOGGER.info("Shutting down processor with %d in queue", self.process_pool._queue_count)
        self.process_pool.shutdown(wait=wait, cancel_futures=cancel_futures)
