import re
from dataclasses import dataclass

UNIT_TO_MINUTES = {
    "w": 7 * 24 * 60,
    "d": 24 * 60,
    "h": 60,
}


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


def duration_to_minutes(value: str) -> int:
    """Converts user-input duration to minutes."""
    if match := re.fullmatch(r"(\d+)([wdh])", value):
        amount = int(match.group(1))
        unit = match.group(2)
        return amount * UNIT_TO_MINUTES[unit]
    raise ValueError("Duration must be in the format <number><unit>, " "where unit is w, d, or h")


def validate_retention_period(value: str) -> str:
    """Validates a retention period."""
    minutes = duration_to_minutes(value)
    max_q_tolerance = 52  # weeks
    max_minutes = max_q_tolerance * UNIT_TO_MINUTES["w"]
    if minutes > max_minutes:
        days = minutes // UNIT_TO_MINUTES["d"]
        text = f"'queue_retention_period' cannot exceed {max_q_tolerance} weeks"
        if value.endswith("w"):
            text += f", received {value.replace('w', ' weeks')}"
        elif value.endswith("d"):
            text += f", received {value.replace('d', ' days')}"
        else:
            text += f", received {value!r} [{days} days]"
        raise ValueError(text)
    return value
