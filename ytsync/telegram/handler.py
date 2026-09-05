import logging
from datetime import datetime, timezone
from ipaddress import IPv4Address

import requests
from pydantic import HttpUrl

from ytsync.modules import config
from ytsync.telegram import webhook

LOGGER = logging.getLogger("ytsync")


async def init() -> None:
    """Initialize telegram API and decide to choose webhook vs long polling."""
    if webhook_is_usable():
        config.telegram_beat.poll_for_messages = False
    else:
        config.telegram_beat.poll_for_messages = True
        LOGGER.info("Polling for incoming messages...")


def webhook_is_usable() -> bool:
    """Check if there is an existing webhook.

    Examples:
        - Successful:

        .. code-block:: json

            {
              "ok": true,
              "result": {
                "url": "https://ytsync.example.com/telegram-webhook",
                "has_custom_certificate": false,
                "pending_update_count": 0,
                "max_connections": 40,
                "ip_address": "198.51.100.42"
              }
            }

        - Unsuccessful:

        .. code-block:: json

            {
              "ok": true,
              "result": {
                "url": "https://ytsync.example.com/telegram-webhook",
                "has_custom_certificate": false,
                "pending_update_count": 1,
                "last_error_date": 1786496692,
                "last_error_message": "Connection refused",
                "max_connections": 40,
                "ip_address": "198.51.100.42"
              }
            }

    Returns:
        bool:
        Returns a boolean flag to indicate webhook availability.
    """
    # 1. Set the webhook
    if all((config.env.bot_webhook, config.env.bot_secret, config.env.bot_webhook_ip)):
        return webhook.set_webhook(
            webhook=config.env.bot_webhook,
            secret_token=config.env.bot_secret,
            webhook_ip=config.env.bot_webhook_ip,
        )
    # 2. Check if there is one setup and look for the error count and how old they were
    max_pending_updates = 100
    max_error_age_seconds = 60
    try:
        existing_webhook = webhook.get_webhook() or {}
        result = existing_webhook.get("result", {}) or {}
        assert isinstance(result, dict), f"Invalid result object received: {result}"
    except (AssertionError, requests.RequestException) as error:
        LOGGER.warning(error)
        return False
    LOGGER.debug(result)
    # This can occur from a previously set webhook that has been deleted from env vars
    if url := result.get("url"):
        config.env.bot_webhook = HttpUrl(str(url))
    if ip_address := result.get("ip_address"):
        config.env.bot_webhook_ip = IPv4Address(str(ip_address))
    # NOTE: This exception handler is only a guard rail, just in case if the payload type changes
    try:
        # Set age to a value higher than the max_error_age if last_error_date is nil
        last_error_date = int(result.get("last_error_date", max_error_age_seconds + 1))
        # Set pending update count to a value lower than the max_pending_updates if pending_update_count is nil
        pending_update_count = int(result.get("pending_update_count", max_pending_updates - 1))
    except (TypeError, ValueError) as error:
        LOGGER.critical(error)
        return False
    last_error_timestamp = datetime.fromtimestamp(
        last_error_date,
        tz=timezone.utc,
    )
    age = (datetime.now(timezone.utc) - last_error_timestamp).total_seconds()
    # 1. Check 'url' to ensure a webhook is configured
    # 2. Check if 'pending_update_count' is less than the max expectation
    # 3. Check if the 'last_error_date' is within the accepted timestamp
    return bool(result.get("url") and pending_update_count < max_pending_updates and age > max_error_age_seconds)
