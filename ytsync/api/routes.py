import asyncio
import logging
from http import HTTPStatus
from json.decoder import JSONDecodeError
from typing import List

import requests
from fastapi import Depends, Request
from fastapi.exceptions import HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from yt_dlp.utils import DownloadError

from ytsync.api import auth, models
from ytsync.database import tracker
from ytsync.modules import config
from ytsync.telegram import bot, poll, webhook
from ytsync.youtube import youtube

LOGGER = logging.getLogger("ytsync")
SECURITY = HTTPBearer(
    description="Enter the telegram bot token (for webhook operations) or apikey (for YTSync interactions)"
)
POLL_LOCK = asyncio.Lock()


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
    if not auth.two_factor(request):
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


async def api_set_webhook(
    body: models.SetWebhook,
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
):
    """API endpoint to POST a webhook.

    Args:
        body: Takes the required webhook parameters as body.
        apikey: API key as header for authentication.
    """
    auth.validate(apikey, True)
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
    """API endpoint to GET a webhook.

    Args:
        apikey: API key as header for authentication.
    """
    auth.validate(apikey, True)
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
    """API endpoint to DELETE a webhook.

    Args:
        apikey: API key as header for authentication.
    """
    auth.validate(apikey, True)
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
    """API endpoint to GET all trackers.

    Args:
        apikey: API key as header for authentication.
    """
    auth.validate(apikey, False)
    if trackers := [track.model_dump(mode="json") for track in tracker.get()]:
        return trackers
    raise HTTPException(status_code=HTTPStatus.NOT_FOUND.real)


async def api_add_trackers(
    body: List[models.Trackers],
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
):
    """API endpoint to ADD new trackers.

    Args:
        body: Takes the required tracker parameters as body.
        apikey: API key as header for authentication.
    """
    auth.validate(apikey, False)
    stats = {}
    for idx, track in enumerate(body):
        try:
            code = tracker.insert(str(track.url), track.schedule, track.chat_id, return_code=True, delay=idx * 1)
        except Exception as error:
            LOGGER.exception(error)
            code = 500
        stats[str(track.url)] = HTTPStatus(value=code)
    return stats


async def api_delete_trackers(
    body: List[models.DeleteTrackers],
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
):
    """API endpoint to DELETE given trackers.

    Args:
        body: Takes the required tracker parameters as body.
        apikey: API key as header for authentication.
    """
    auth.validate(apikey, False)
    if not body:
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST.real)
    trackers = list(tracker.get())
    stats = {}
    for track in body:
        if not any((track.name, track.url)):
            raise HTTPException(
                status_code=HTTPStatus.BAD_REQUEST.real, detail="Either name or url is required for each entry."
            )
        try:
            code = tracker.delete(
                name=track.name, url=track.url, chat_id=track.chat_id, return_code=True, trackers=trackers
            )
        except Exception as error:
            LOGGER.exception(error)
            code = 500
        stats[track.name or track.url] = HTTPStatus(value=code)
    return stats


async def api_sync_trackers(
    body: List[models.SyncTrack],
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
):
    """API endpoint to SYNC playlists as an on-demand request.

    Args:
        body: List of URL objects as request body.
        apikey: APIkey to authenticate the request.
    """
    auth.validate(apikey, False)
    # TODO: Bad request should provide detail or give a link to the '/docs' page based on the request.url
    if not body:
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST.real)
    stat = {}
    for track in body:
        try:
            stat[track.url] = dict(
                status_code=200,
                detail=await asyncio.wait_for(
                    youtube.queue_download(
                        playlist_url=str(track.url), chat_id=track.chat_id, callback=bot.reply_to, raw_text=True
                    ),
                    timeout=config.env.response_timeout,
                ),
            )
        except (ValueError, AssertionError, DownloadError) as error:
            LOGGER.exception(error)
            stat[track.url] = dict(status_code=500, detail=str(error))
    return stat
