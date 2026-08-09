import logging
from ipaddress import IPv4Address
from typing import Dict

import requests
from pydantic import HttpUrl

from yt2jf.bot import BASE_URL
from yt2jf.config import env
from yt2jf.exceptions import EgressErrors

LOGGER = logging.getLogger("uvicorn.default")


def get_webhook() -> Dict[str, str] | None:
    """Get webhook information.

    References:
        https://core.telegram.org/bots/api#getwebhookinfo
    """
    get_info = f"{BASE_URL}/getWebhookInfo"
    response = requests.get(url=get_info)
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
    del_info = f"{BASE_URL}/setWebhook"
    response = requests.post(url=del_info, params=dict(url=None))
    if response.ok:
        LOGGER.info("Webhook has been removed.")
        return response.json()
    response.raise_for_status()
    return None


def set_webhook(
    webhook: HttpUrl | str,
    webhook_ip: IPv4Address | None = None,
) -> bool | None:
    """Set webhook.

    References:
        https://core.telegram.org/bots/api#setwebhook
    """
    put_info = f"{BASE_URL}/setWebhook"
    payload = dict(url=webhook, secret_token=env.bot_secret)
    if webhook_ip:
        payload["ip_address"] = webhook_ip.__str__()
    LOGGER.debug(payload)
    try:
        if env.bot_certificate:
            response = requests.post(
                url=put_info,
                data=payload,
                files={
                    "certificate": (
                        env.bot_certificate.stem + env.bot_certificate.suffix,
                        env.bot_certificate.certificate.open(mode="rb"),
                    )
                },
            )
        else:
            response = requests.post(url=put_info, params=payload)
        response.raise_for_status()
        if response.ok:
            LOGGER.info("Webhook has been set to: %s", webhook)
            LOGGER.info(response.json())
            return response.ok
    except EgressErrors as error:
        LOGGER.error(error)
    return None
