import asyncio
import logging
import secrets
from http import HTTPStatus
from ipaddress import IPv4Address
from json.decoder import JSONDecodeError
from typing import List

import requests
from fastapi import Depends, Request
from fastapi.exceptions import HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, HttpUrl

from ytsync.database import tracker
from ytsync.modules import config
from ytsync.telegram import bot, poll, webhook

LOGGER = logging.getLogger("ytsync")
SECURITY = HTTPBearer(description="Enter your telegram username")
POLL_LOCK = asyncio.Lock()
# TODO: Add more description for API functions


def two_factor(request: Request) -> bool:
    """Two factor verification for messages coming via webhook.

    Args:
        request: Request object from FastAPI.

    Returns:
        bool:
        Flag to indicate the calling function if the auth was successful.
    """
    if config.env.bot_secret:
        if secrets.compare_digest(
            request.headers.get("X-Telegram-Bot-Api-Secret-Token", ""),
            config.env.bot_secret,
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
    # noinspection unresolved-references
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
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN.real, detail=HTTPStatus.FORBIDDEN.phrase)
    if payload := response.get("message"):
        LOGGER.debug(response)
        await bot.process_request(payload)
    else:
        raise HTTPException(
            status_code=HTTPStatus.UNPROCESSABLE_ENTITY.real,
            detail=HTTPStatus.UNPROCESSABLE_ENTITY.phrase,
        )


class SetWebhook(BaseModel):
    """Request payload for POST webhook endpoint."""

    webhook: HttpUrl
    secret_token: str
    webhook_ip: IPv4Address | None = None


async def api_set_webhook(
    body: SetWebhook,
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
):
    """API endpoint to POST a webhook."""
    if not secrets.compare_digest(apikey.credentials, config.env.bot_token):
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED.real,
        )
    # No webhook received
    if not body.webhook:
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST.real,
        )
    # Invalid URL scheme - only 'https' is accepted
    if body.webhook.scheme != "https":
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST.real, detail="Invalid URL scheme")
    # Invalid URL path - only the path served by the API (via env.bot_endpoint) is accepted
    if body.webhook.path != config.env.bot_endpoint:
        LOGGER.warning(
            "Invalid webhook path received. Expected: '%s'; received: '%s'",
            config.env.bot_endpoint,
            body.webhook.path,
        )
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST.real, detail="Invalid URL path")

    if webhook.set_webhook(
        webhook=body.webhook,
        secret_token=body.secret_token,
        webhook_ip=body.webhook_ip,
    ):
        config.env.bot_webhook = body.webhook
        config.env.bot_secret = body.secret_token
        config.env.bot_webhook_ip = body.webhook_ip
        async with POLL_LOCK:
            await poll.stop_polling()
        raise HTTPException(
            status_code=HTTPStatus.OK.real,
        )
    raise HTTPException(
        status_code=HTTPStatus.EXPECTATION_FAILED.real,
    )


async def api_get_webhook(
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
):
    """API endpoint to GET a webhook."""
    if not secrets.compare_digest(apikey.credentials, config.env.bot_token):
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED.real,
        )
    try:
        return webhook.get_webhook()
    except requests.RequestException as error:
        LOGGER.error(error)
        raise HTTPException(
            status_code=HTTPStatus.EXPECTATION_FAILED.real,
        )


async def api_delete_webhook(
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
):
    """API endpoint to DELETE a webhook."""
    if not secrets.compare_digest(apikey.credentials, config.env.bot_token):
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED.real,
        )
    try:
        response = webhook.delete_webhook()
        async with POLL_LOCK:
            await poll.stop_polling()
            poll.start_polling()
        return response
    except requests.RequestException as error:
        LOGGER.error(error)
        raise HTTPException(
            status_code=HTTPStatus.EXPECTATION_FAILED.real,
        )


async def api_get_trackers(
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
):
    """API endpoint to GET all trackers."""
    if not secrets.compare_digest(apikey.credentials, config.env.apikey):
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED.real,
        )
    if trackers := tracker.get():
        return [
            dict(index=idx, url=url, name=name, schedule=schedule) for idx, (url, name, schedule) in enumerate(trackers)
        ]
    raise HTTPException(status_code=HTTPStatus.NOT_FOUND.real)


class Trackers(BaseModel):
    """Payload to add trackers through API."""

    urls: List[HttpUrl]


async def api_add_trackers(
    body: Trackers,
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
):
    """API endpoint to ADD new trackers."""
    if not secrets.compare_digest(apikey.credentials, config.env.apikey):
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED.real,
        )
    stats = {}
    for url in body.urls:
        try:
            code = tracker.insert(str(url), return_code=True)
        except Exception as error:
            LOGGER.error(error)
            code = 500
        stats[url] = HTTPStatus(value=code)
    return stats


async def api_delete_trackers(
    indices: str,
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
):
    """API endpoint to DELETE given trackers."""
    if not secrets.compare_digest(apikey.credentials, config.env.apikey):
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED.real,
        )
    try:
        indices = [int(idx) for idx in indices.split(",")]
    except ValueError:
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST)
    stats = {}
    for idx in indices:
        try:
            code = tracker.delete(idx, return_code=True)
        except Exception as error:
            LOGGER.error(error)
            code = 500
        stats[idx] = HTTPStatus(value=code)
    return stats
