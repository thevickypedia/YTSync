import logging
from ipaddress import IPv4Address
from typing import Dict

import httpx
from pydantic import HttpUrl

from ytsync.modules import config
from ytsync.telegram import bot

LOGGER = logging.getLogger("ytsync")
webhook_timeout = httpx.Timeout(None, connect=3, read=10)


async def get_webhook() -> Dict[str, str] | None:
    """Get webhook information.

    References:
        https://core.telegram.org/bots/api#getwebhookinfo
    """
    get_info = f"{bot.BASE_URL}/getWebhookInfo"
    response = await config.ASYNC_CLIENT.get(url=get_info, timeout=webhook_timeout)
    if response.is_success:
        LOGGER.info(response.json())
        return response.json()
    response.raise_for_status()
    return None


async def delete_webhook() -> Dict[str, str] | None:
    """Delete webhook.

    References:
        https://core.telegram.org/bots/api#deletewebhook
    """
    del_info = f"{bot.BASE_URL}/setWebhook"
    response = await config.ASYNC_CLIENT.post(url=del_info, params=dict(url=None), timeout=webhook_timeout)
    if response.is_success:
        LOGGER.info("Webhook has been removed.")
        return response.json()
    response.raise_for_status()
    return None


async def set_webhook(
    webhook: HttpUrl,
    secret_token: str,
    webhook_ip: IPv4Address | None = None,
) -> bool:
    """Set webhook.

    Args:
        webhook: The webhook URL to set.
        secret_token: The secret token to set for the webhook.
        webhook_ip: The IP address to set for the webhook.

    References:
        https://core.telegram.org/bots/api#setwebhook
    """
    put_info = f"{bot.BASE_URL}/setWebhook"
    payload = dict(url=str(webhook), secret_token=secret_token)
    if webhook_ip:
        payload["ip_address"] = webhook_ip.__str__()
    LOGGER.debug(payload)
    try:
        if config.env.bot_certificate:
            response = await config.ASYNC_CLIENT.post(
                url=put_info,
                data=payload,
                files={
                    "certificate": (
                        config.env.bot_certificate.stem + config.env.bot_certificate.suffix,
                        config.env.bot_certificate.certificate.open(mode="rb"),
                    )
                },
            )
        else:
            # noinspection bad-argument-type
            response = await config.ASYNC_CLIENT.post(url=put_info, params=payload, timeout=webhook_timeout)
        response.raise_for_status()
        if response.is_success:
            LOGGER.info("Webhook has been set to: %s", webhook)
            LOGGER.info(response.json())
            return response.is_success
    except httpx.HTTPError as error:
        LOGGER.error(error)
    return False
