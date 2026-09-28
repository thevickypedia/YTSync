import re
from dataclasses import dataclass
from typing import Tuple


@dataclass
class TelegramBeat:
    """Telegram beat class.

    >>> TelegramBeat

    """

    offset: int = 0
    failed_connections: int = 0

    polling_in_progress: bool = False
    poll_for_messages: bool = False
    restart_loop: bool = False


def duration_to_days(value: str) -> Tuple[int, str]:
    """Converts input duration to days.

    Args:
        value: Input duration.

    Returns:
        Tuple[int, str]:
        Tuple of days and formatted string that includes the expanded original value.
    """
    if match := re.fullmatch(r"(\d+)([dwm])", value):
        amount = int(match.group(1))
        unit = match.group(2)
        if unit == "d":
            return amount, value.replace("d", " days")
        if unit == "w":
            return amount * 7, f"{value.replace('w', ' weeks')} [{amount * 7} days]"
        if unit == "m":
            return amount * 30, f"{value.replace('m', ' months')} [{amount * 30} days]"
    raise ValueError("Duration must be in the format <number><unit>, " "where unit is d, w, or m")


def validate_retention_period(value: str) -> str:
    """Validates a retention period."""
    days_int, days_str = duration_to_days(value)
    max_tolerance = 90
    if days_int > max_tolerance:
        raise ValueError(f"'checkpoint_retention' cannot exceed {max_tolerance} days, received {days_str}")
    return value
