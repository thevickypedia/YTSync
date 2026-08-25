import asyncio
import logging
from http import HTTPStatus
from json.decoder import JSONDecodeError

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
    """**API endpoint to POST a webhook.**

    **Args**

        ‣‣ body: Takes the required webhook parameters as body.

    **Notes**

        ‣‣ 'webhook' endpoint should match the 'bot_endpoint' environment variable set during startup.
        ‣‣ 'secret_token' is required to authenticate the incoming request to avoid man-in-the-middle attacks.
        ‣‣ 'webhook_ip' is optional; useful for bots behind a NAT or complex network configurations.

    **Examples**

        {
            "webhook": "https://webhook.example.com/telegram-webhook",
            "secret_token": "TelegramWillIncludeThisInTheWebhookRequest",
            "webhook_ip": "198.51.100.42"
        }
    """
    auth.validate(apikey, True)
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
    """**API endpoint to GET a webhook.**"""
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
    """**API endpoint to DELETE a webhook.**"""
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
    """**API endpoint to GET all trackers.**"""
    auth.validate(apikey, False)
    if trackers := [track.model_dump(mode="json") for track in tracker.get()]:
        # Include 'chat_id' in the payload ONLY when authenticated with telegram bot token
        if apikey.credentials == config.env.apikey:
            return trackers
        cleaned = []
        for tr in trackers:
            tr.pop("chat_id", None)
            cleaned.append(tr)
        return cleaned
    raise HTTPException(status_code=HTTPStatus.NOT_FOUND.real)


async def api_add_trackers(
    body: models.Trackers,
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
):
    """**API endpoint to ADD new trackers.**

    **Args**

        ‣‣ body: Takes the required tracker parameters as body.

    **Notes**

        ‣‣ Body must be a list of dict - with url, schedule, and chat id (optional) as key-value pairs.
        ‣‣ 'url' can be any YouTube domain URL, as long as there is an audio to extract.
        ‣‣ 'schedule' must be @hourly, @daily, @weekly, or @monthly as a string.
        ‣‣ 'chat_id' is optional to send a telegram notification everytime the scheduled run completes/fails.

    **Examples**

        {
            "url": "https://music.youtube.com/playlist?list=OLAK5uy_nQY-UERFpsL1d5UTPVMjX7mtnVlKg7D4w",
            "schedule": "@weekly",
            "chat_id": 1234567890
        }
    """
    auth.validate(apikey, False)
    tracker.insert(str(body.url), body.schedule, body.chat_id, raise_for_exception=True)


async def api_delete_trackers(
    body: models.DeleteTrackers,
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
):
    """**API endpoint to DELETE given trackers.**

    **Args**

        ‣‣ body: Takes the required tracker parameters as body.

    **Notes**

        ‣‣ Body must be a dictionary with (name or url), and chat id (optional) as key-value pairs.
        ‣‣ Either 'name' or 'url' can be used as identifier.
        ‣‣ 'name' to identify and delete the tracker.
        ‣‣ 'url' to identify and delete the tracker.
        ‣‣ 'chat_id' the tracker was requested with. If the original request was an API call, set it to 0.

    **Examples**

        {
            "name": "Encore",
            "chat_id" 0
        }
    """
    auth.validate(apikey, False)
    if not any((body.name, body.url)):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST.real, detail="Either name or url is required for each entry."
        )
    tracker.delete(name=body.name, url=body.url, chat_id=body.chat_id, raise_for_exception=True)


async def download(
    body: models.Download,
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
):
    """**API endpoint to download playlists as an on-demand request.**

    **Args**

        ‣‣ body: List of Download object as request body.

    **Notes**

        ‣‣ Body must be a dictionary with url, and chat id (optional) as key-value pairs.
        ‣‣ 'url' can be any YouTube domain URL, as long as there is an audio to extract.
        ‣‣ 'chat_id' is optional to send a telegram notification when the download completes/fails.

    **Examples**

        {
            "url": "https://music.youtube.com/playlist?list=OLAK5uy_nQY-UERFpsL1d5UTPVMjX7mtnVlKg7D4w",
            "chat_id": 1234567890
        }
    """
    auth.validate(apikey, False)
    try:
        response = await asyncio.wait_for(
            youtube.queue_download(
                playlist_url=str(body.url), chat_id=body.chat_id, callback=bot.reply_to, raw_text=True
            ),
            timeout=config.env.response_timeout,
        )
        raise HTTPException(status_code=HTTPStatus.OK.real, detail=response)
    except (ValueError, AssertionError, DownloadError) as error:
        LOGGER.exception(error)
        raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR.real, detail=str(error))
