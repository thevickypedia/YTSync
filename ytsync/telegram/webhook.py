import logging
from ipaddress import IPv4Address
from typing import Dict

import requests
from pydantic import HttpUrl

from ytsync.modules import config, exceptions
from ytsync.telegram import bot

LOGGER = logging.getLogger("ytsync")


def get_webhook() -> Dict[str, str] | None:
    """Get webhook information.

    References:
        https://core.telegram.org/bots/api#getwebhookinfo
    """
    get_info = f"{bot.BASE_URL}/getWebhookInfo"
    response = requests.get(url=get_info, timeout=(3, 10))
    if response.ok:
        LOGGER.info(response.json())
        return response.json()
    response.raise_for_status()
    return None


def delete_webhook() -> Dict[str, str] | None:
    """Delete webhook.

    References:
        https://core.telegram.org/bots/api#deletewebhook
    """
    del_info = f"{bot.BASE_URL}/setWebhook"
    response = requests.post(url=del_info, params=dict(url=None))
    if response.ok:
        LOGGER.info("Webhook has been removed.")
        return response.json()
    response.raise_for_status()
    return None


def set_webhook(
    webhook: HttpUrl,
    secret_token: str,
    webhook_ip: IPv4Address | None = None,
) -> bool | None:
    """Set webhook.

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
            response = requests.post(
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
            response = requests.post(url=put_info, params=payload)
        response.raise_for_status()
        if response.ok:
            LOGGER.info("Webhook has been set to: %s", webhook)
            LOGGER.info(response.json())
            return response.ok
    except exceptions.EgressErrors as error:
        LOGGER.error(error)
    return None
