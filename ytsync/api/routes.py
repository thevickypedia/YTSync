import asyncio
import logging
import time
from datetime import datetime
from http import HTTPStatus
from json.decoder import JSONDecodeError
from typing import Dict, List

import httpx
from fastapi import Depends, Request, Response
from fastapi.exceptions import HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ValidationError
from yt_dlp.utils import DownloadError

from ytsync.api import auth, models
from ytsync.database import queue, tracker
from ytsync.modules import config
from ytsync.telegram import bot
from ytsync.telegram import models as telegram_models
from ytsync.telegram import webhook
from ytsync.youtube import checkpoint, youtube

LOGGER = logging.getLogger("ytsync")
SECURITY = HTTPBearer(
    description="Enter the telegram bot token (for webhook operations) or apikey (for YTSync interactions)"
)


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
    if not await auth.two_factor(request):
        LOGGER.error("Request received from a non-webhook source")
        LOGGER.error(response)
        raise HTTPException(status_code=HTTPStatus.FORBIDDEN.real, detail=HTTPStatus.FORBIDDEN.phrase)
    if response.get("healthcheck", False) is True:
        LOGGER.info("Received a healthcheck request")
        return {"status": "ok"}
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
    """
    await auth.validate_bot_request(apikey)
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

    if await webhook.set_webhook(
        webhook=body.webhook,
        secret_token=body.secret_token,
        webhook_ip=body.webhook_ip,
    ):
        config.env.bot_webhook = body.webhook
        config.env.bot_secret = body.secret_token
        config.env.bot_webhook_ip = body.webhook_ip
        config.telegram_beat.poll_for_messages = False
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
    await auth.validate_bot_request(apikey)
    try:
        return await webhook.get_webhook()
    except httpx.HTTPError as error:
        LOGGER.error(error)
        raise HTTPException(
            status_code=HTTPStatus.EXPECTATION_FAILED.real,
        )


async def api_delete_webhook(
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
):
    """**API endpoint to DELETE a webhook.**"""
    await auth.validate_bot_request(apikey)
    try:
        response = await webhook.delete_webhook()
        config.telegram_beat.poll_for_messages = True
        return response
    except httpx.HTTPError as error:
        LOGGER.error(error)
        raise HTTPException(
            status_code=HTTPStatus.EXPECTATION_FAILED.real,
        )


async def api_get_trackers(
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
) -> List[tracker.DBSchema]:
    """**API endpoint to GET all trackers.**"""
    profile = await auth.validate_api_request(apikey)
    return [item async for item in tracker.get(profile.name)]


async def api_add_trackers(
    body: models.Trackers,
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
) -> None:
    """**API endpoint to ADD new trackers.**

    **Args**

        ‣‣ body: Takes the required tracker parameters as body.

    **Notes**

        ‣‣ Body must be a list of dict - with url, schedule, and chat id (optional) as key-value pairs.
        ‣‣ 'url' can be any YouTube domain URL, as long as there is an audio to extract.
        ‣‣ 'schedule' must be @hourly, @daily, @weekly, or @monthly as a string.
        ‣‣ 'chat_id' is optional to send a telegram notification everytime the scheduled run completes/fails.
    """
    profile = await auth.validate_api_request(apikey)
    await tracker.insert(
        profile_name=profile.name,
        playlist_url=body.url,
        schedule=body.schedule,
        chat_id=body.chat_id,
        raise_for_exception=True,
    )


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
    """
    profile = await auth.validate_api_request(apikey)
    if not any((body.name, body.url)):
        raise HTTPException(
            status_code=HTTPStatus.BAD_REQUEST.real, detail="Either name or url is required for each entry."
        )
    await tracker.delete(
        profile_name=profile.name, name=body.name, url=body.url, chat_id=body.chat_id, raise_for_exception=True
    )


async def telegram_source(chat_id: int, profile_name: str) -> telegram_models.Chat:
    """Creates a Telegram Chat object for the given chat_id.

    Args:
        chat_id: Chat ID of the user to send the notification to.
        profile_name: Name of the profile associated with the request.

    Returns:
        telegram_models.Chat:
        A Chat object with the provided chat_id and profile_name.
    """
    return telegram_models.Chat(
        message_id=0,  # API trigger will not have an actual 'message_id' to respond to
        date=int(time.time()),
        id=chat_id,  # Assuming user's input is a valid chat_id
        username=profile_name,  # Placeholder
        is_bot=False,
    )


async def download(
    request: Request,
    body: models.Download,
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
):
    """**API endpoint to download any YT content based on the URL as an on-demand request.**

    **Args**

        ‣‣ body: List of Download object as request body.

    **Notes**

        ‣‣ Body must be a dictionary with url, and chat id (optional) as key-value pairs.
        ‣‣ 'url' can be any YouTube domain URL, as long as there is an audio to extract.
        ‣‣ 'chat_id' is optional to send a telegram notification when the download completes/fails.
    """
    profile = await auth.validate_api_request(apikey)
    try:
        if body.chat_id:
            # Priority 1: If the request body has a chat_id, use it to create a Telegram source system
            source = checkpoint.SourceSystem(
                profile_name=profile.name,
                telegram=await telegram_source(body.chat_id, profile.name),
                audio_only=body.audio_only,
            )
        elif profile.bot_chat_id:
            # Priority 2: If the profile has a bot_chat_id, use it to create a Telegram source system
            source = checkpoint.SourceSystem(
                profile_name=profile.name,
                telegram=await telegram_source(profile.bot_chat_id, profile.bot_username or profile.name),
                audio_only=body.audio_only,
            )
        else:
            # Fallback: Use the request's client host and headers to create an API source system
            source = checkpoint.SourceSystem(
                profile_name=profile.name,
                api=checkpoint.APISource(
                    host=request.client.host,
                    host_header=request.headers.get("host"),
                ),
                audio_only=body.audio_only,
            )
        response = await asyncio.wait_for(
            youtube.queue_download(url=body.url, source_system=source),
            timeout=config.env.response_timeout,
        )
        raise HTTPException(status_code=HTTPStatus.OK.real, detail=response)
    except (ValueError, AssertionError, DownloadError) as error:
        LOGGER.exception(error)
        raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR.real, detail=str(error))


async def list_checkpoints(
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
) -> Dict[str, List[int]]:
    """**API endpoint to list all checkpoints.**

    **Sample Response**

        {
          "Aug_29_2026": [
            "1788010080",
            "1788013828",
            "1788024673"
          ]
        }
    """
    profile = await auth.validate_api_request(apikey)
    return {k: v async for k, v in checkpoint.ls(profile.name)}


async def get_checkpoint(
    datestamp: str,
    timestamp: int,
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
) -> checkpoint.Checkpoint:
    """**API endpoint to get a specific checkpoint.**

    **Args**

        ‣‣ datestamp: Datestamp of the checkpoint. Example: Aug_29_2026 (directory name)
        ‣‣ timestamp: Timestamp of the checkpoint. Example: 1788010080 (file name identifier)
    """
    profile = await auth.validate_api_request(apikey)
    if not config.is_valid_checkpoint_dir(datestamp):
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND.real,
            detail=f"Checkpoint directory {datestamp} not found",
        )
    try:
        return checkpoint.get(profile.name, datestamp, int(timestamp))
    except FileNotFoundError as error:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND.real, detail=error)
    except ValidationError as error:
        LOGGER.exception(error)
        raise HTTPException(
            status_code=HTTPStatus.INTERNAL_SERVER_ERROR.real, detail=HTTPStatus.INTERNAL_SERVER_ERROR.description
        )


async def delete_checkpoint(
    datestamp: str,
    timestamp: int,
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
):
    """**API endpoint to delete a specific checkpoint.**

    **Args**

        ‣‣ datestamp: Datestamp of the checkpoint. Example: Aug_29_2026 (directory name)
        ‣‣ timestamp: Timestamp of the checkpoint. Example: 1788010080 (file name identifier)
    """
    profile = await auth.validate_api_request(apikey)
    if not config.is_valid_checkpoint_dir(datestamp):
        raise HTTPException(
            status_code=HTTPStatus.NOT_FOUND.real,
            detail=f"Checkpoint directory {datestamp} not found",
        )
    try:
        checkpoint.delete(profile.name, datestamp, int(timestamp))
    except FileNotFoundError as error:
        raise HTTPException(status_code=HTTPStatus.NOT_FOUND.real, detail=error)
    except NotADirectoryError as error:
        raise HTTPException(status_code=HTTPStatus.BAD_REQUEST.real, detail=error)
    except OSError as error:
        LOGGER.exception(error)
        raise HTTPException(status_code=HTTPStatus.INTERNAL_SERVER_ERROR.real, detail=error)


async def get_queue(
    response: Response,
    include_history: bool = False,
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
) -> List[queue.Queue]:
    """**API endpoint to get the current queue.**

    **Args**

        ‣‣ include_history: Boolean flag to include past queue objects.
    """
    profile = await auth.validate_api_request(apikey)
    queued_items = [item async for item in queue.get(profile.name, include_history)]
    response.headers["total-count"] = str(len(queued_items))
    return queued_items


async def add_queue(
    payload: queue.Queue,
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
):
    """**API endpoint to add a queue item into the database.**

    **Args**

        ‣‣ payload: Queue object stored in the database.
    """
    profile = await auth.validate_api_request(apikey)
    await queue.insert(profile.name, payload)
    return {"ok": True}


async def delete_queue(
    scheduled_time: datetime,
    apikey: HTTPAuthorizationCredentials = Depends(SECURITY),
):
    """**API endpoint to delete a queue from the database.**

    **Args**

        ‣‣ scheduled_time: Queue item's scheduled time.
    """
    profile = await auth.validate_api_request(apikey)
    if await queue.delete(profile.name, str(scheduled_time)):
        return {"ok": True}
    raise HTTPException(
        status_code=HTTPStatus.NOT_FOUND.real,
        detail=f"Queue item with scheduled_time {scheduled_time} not found",
    )
