import asyncio
import logging
import secrets
from http import HTTPStatus
from ipaddress import IPv4Address
from json.decoder import JSONDecodeError
from typing import Dict

import requests
from fastapi import Depends, Request
from fastapi.exceptions import HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import HttpUrl

from yt2jf.bot import process_request
from yt2jf.config import env
from yt2jf.webhook import delete_webhook, get_webhook, set_webhook

LOGGER = logging.getLogger("uvicorn.default")
SECURITY = HTTPBearer(description="Enter your telegram username")
ACTIVE_TASKS: Dict[str, asyncio.Task] = {}


def two_factor(request: Request) -> bool:
    """Two factor verification for messages coming via webhook.

    Args:
        request: Request object from FastAPI.

    Returns:
        bool:
        Flag to indicate the calling function if the auth was successful.
    """
    if env.bot_secret:
        if secrets.compare_digest(
            request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""),
            env.bot_secret,
        ):
            return True
    else:
        LOGGER.warning("Use the env var bot_secret to secure the webhook interaction")
        return True
    return False


async def telegram_webhook(request: Request):
    """Invoked when a new message is received from Telegram API.

    Args:
        request: Request instance.

    Raises:

        HTTPException:
            - 406: If the request payload is not JSON format-able.
    """
    LOGGER.debug(
        "Connection received from %s via %s",
        request.client.host,
        request.headers.get("host"),
    )
    try:
        response = await request.json()
    except JSONDecodeError as error:
        LOGGER.error(error)
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST.real,
            detail=HTTPStatus.BAD_REQUEST.phrase,
        )
    # Ensure only the owner who set the webhook can interact with the Bot
    if not two_factor(request):
        LOGGER.error("Request received from a non-webhook source")
        LOGGER.error(response)
        raise HTTPException(
            status_code=HTTPStatus.FORBIDDEN.real, detail=HTTPStatus.FORBIDDEN.phrase
        )
    if payload := response.get("message"):
        LOGGER.debug(response)
        process_request(payload)
    else:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY.real,
            detail=HTTPStatus.UNPROCESSABLE_ENTITY.phrase,
        )


async def api_set_webhook(
    webhook: HttpUrl,
    webhook_ip: IPv4Address | None = None,
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
):
    """API endpoint to POST a webhook."""
    if not secrets.compare_digest(apikey.credentials, env.bot_secret):
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED.real,
        )
    if webhook:
        if not webhook.scheme == "https":
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST.real,
            )
        if set_webhook(webhook=webhook, webhook_ip=webhook_ip):
            env.bot_webhook = webhook
            task = ACTIVE_TASKS["poll"]
            if not task.done():
                task.cancel("webhook has been set")
            raise HTTPException(
                status_code=HTTPStatus.OK.real,
            )
        raise HTTPException(
            status_code=HTTPStatus.EXPECTATION_FAILED.real,
        )
    raise HTTPException(
        status_code=HTTPStatus.BAD_REQUEST.real,
    )


async def api_get_webhook(
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
):
    """API endpoint to GET a webhook."""
    if not secrets.compare_digest(apikey.credentials, env.bot_secret):
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED.real,
        )
    try:
        return get_webhook()
    except requests.RequestException as error:
        LOGGER.error(error)
        raise HTTPException(
            status_code=HTTPStatus.EXPECTATION_FAILED.real,
        )


async def api_delete_webhook(
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
):
    """API endpoint to DELETE a webhook."""
    if not secrets.compare_digest(apikey.credentials, env.bot_secret):
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED.real,
        )
    try:
        return delete_webhook()
    except requests.RequestException as error:
        LOGGER.error(error)
        raise HTTPException(
            status_code=HTTPStatus.EXPECTATION_FAILED.real,
        )
