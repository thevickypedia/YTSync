import re
from dataclasses import dataclass


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


def duration_to_days(value: str) -> int:
    """Converts user-input duration to days."""
    if match := re.fullmatch(r"(\d+)([dwm])", value):
        amount = int(match.group(1))
        unit = match.group(2)
        if unit == "d":
            return amount
        if unit == "w":
            return amount * 7
        if unit == "m":
            return amount * 30
    raise ValueError("Duration must be in the format <number><unit>, " "where unit is d, w, or m")


def validate_retention_period(value: str) -> str:
    """Validates a retention period."""
    days = duration_to_days(value)
    max_tolerance = 90
    if days > max_tolerance:
        text = "'checkpoint_retention_period' cannot exceed 90 days"
        if value.endswith("d"):
            text += f", received {value.replace('d', ' days')}"
        elif value.endswith("w"):
            text += f", received {value.replace('w', ' weeks')} [{days} days]"
        elif value.endswith("m"):
            text += f", received {value.replace('m', ' months')} [{days} days]"
        raise ValueError(text)
    return value
