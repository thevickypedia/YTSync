import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import PositiveInt

from ytsync.modules import config

LOGGER = logging.getLogger("ytsync")


@dataclass
class RetryResponse:
    """Response class for the retry logic.

    >>> RetryResponse

    """

    response: Any | None
    attempts: PositiveInt


def retry(
    function: Callable,
    max_retries: int = config.env.max_retries,
    backoff_factor: int = config.env.backoff_factor,
    raise_error: bool = False,
    *args,
    **kwargs,
) -> RetryResponse:
    """Re-usable retry logic with timed delay and an exponential back-off factor.

    Args:
        function: Function to call.
        max_retries: Number of retries to attempt before exhausting.
        backoff_factor: Number of seconds to use for exponential delay between each retry attempt.
        raise_error: Boolean flag to raise an error after all retry attempts.
    """
    attempt = 0
    result = None
    while attempt < max_retries:
        try:
            result = function(*args, **kwargs)
            break
        except Exception as error:
            attempt += 1
            if attempt < max_retries:
                # Calculate exponential backoff: 3s, 6s, 12s, 24s...
                delay = backoff_factor * (2 ** (attempt - 1))
                LOGGER.warning(
                    f"Error occurred on {function.__name__!r} (Attempt {attempt}/{max_retries}). "
                    f"Retrying in {delay}s..."
                )
                LOGGER.warning(f"Error: {error}")
                time.sleep(delay)
            else:
                LOGGER.error(
                    f"Error occurred on {function.__name__!r} after {max_retries} attempts due to repeated exceptions."
                )
                LOGGER.error(f"Final Error: {error}")
                if raise_error:
                    raise RuntimeError(f"{type(error).__name__}: {error}") from None
                break
    return RetryResponse(
        response=result,
        attempts=attempt,
    )
