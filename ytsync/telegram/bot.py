# noinspection PyUnresolvedReferences
"""Module for TelegramAPI.

>>> Bot

"""

import asyncio
import logging
import secrets
import sys
import time
from datetime import datetime
from enum import StrEnum
from typing import Dict, List

import requests
from pydantic import HttpUrl, ValidationError
from yt_dlp.utils import DownloadError

from ytsync.database import tracker
from ytsync.modules import checkpoint, config, exceptions, settings
from ytsync.youtube import youtube

BASE_URL = f"https://api.telegram.org/bot{config.env.bot_token}"
LOGGER = logging.getLogger("ytsync")


class RequestMethods(StrEnum):
    """Allowed request methods.

    >>> RequestMethods

    """

    GET = "GET"
    POST = "POST"


class Commands(StrEnum):
    """Allowed Telegram bot commands.

    >>> Commands

    """

    start = "/start"
    help = "/help"
    status = "/status"

    audio = "/audio"
    video = "/video"
    track = "/track"
    sync = "/sync"
    delete = "/delete"


def get_help():
    """Get the help text for telegram interactions."""
    return (
        "🎵 *YTSync Bot Commands*\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"⬇️ *{Commands.audio}* `<URL>`\n"
        f"⬇️ *{Commands.video}* `<URL>`\n"
        "    Download content from any YT url.\n\n"
        f"⏱️ *{Commands.track}* `<URL>` `{' | '.join(config.AllowedCronSchedule.__members__)}`\n"
        "    Track a URL on a recurring schedule.\n\n"
        f"🔄 *{Commands.sync}* `<NAME>` [OR] `<URL>`\n"
        "    Ad-hoc sync an existing tracker by name or URL.\n\n"
        f"🗑️ *{Commands.delete}* `<NAME>` [OR] `<URL>`\n"
        "    Delete an existing tracker by name or URL.\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "💡 *Tip:* Arguments in `<angle brackets>` are required."
    )


def intro() -> str:
    """Returns a welcome message as a string.

    Returns:
        str:
    """
    return f"\nTo start, send any YT link in the following format:\n\n{get_help()}"


def _make_request(
    url: str,
    payload: dict,
    files: dict | None = None,
    method: RequestMethods = RequestMethods.POST,
) -> requests.Response:
    """Makes a POST request with a ``connect timeout`` of 5 seconds and ``read timeout`` of 60.

    Args:
        url: URL to submit the request.
        payload: Payload received, to extract information from.
        files: Take filename as an optional argument.

    Returns:
        Response:
        Response class.
    """
    if method == RequestMethods.GET:
        response = requests.get(url=url, data=payload, files=files, timeout=(2, 3))
    elif method == RequestMethods.POST:
        response = requests.post(url=url, data=payload, files=files, timeout=(2, 3))
    else:
        raise ValueError("Invalid request method received: '%s'", method)
    if not response.ok:
        LOGGER.debug(payload)
        LOGGER.debug(files)
        LOGGER.warning("Called by: '%s'", sys._getframe(1).f_code.co_name)  # noqa
        LOGGER.error(response.json())
    return response


def reply_to(
    chat_id: int,
    message_id: int | None,
    response: str,
    parse_mode: str | None = "markdown",
    retry: bool = False,
) -> requests.Response:
    """Generates a payload to reply to a message received.

    Args:
        chat_id: ChatId to respond to.
        message_id: MessageId to mark as reply.
        response: Message to be sent to the user.
        parse_mode: Parse mode. Defaults to ``markdown``
        retry: Retry reply in case reply failed because of parsing.

    Returns:
        Response:
        Response class.
    """
    if not message_id:
        return send_message(chat_id, response, parse_mode)
    result = _make_request(
        url=BASE_URL + "/sendMessage",
        payload={
            "chat_id": chat_id,
            "reply_to_message_id": message_id,
            "text": response,
            "parse_mode": parse_mode,
        },
    )
    # Retry with response as plain text
    if result.status_code == 400 and parse_mode and not retry:
        LOGGER.warning("Retrying response as plain text with no parsing")
        reply_to(chat_id, message_id, response, None, True)
    return result


def send_message(
    chat_id: int,
    response: str,
    parse_mode: str | None = "markdown",
    retry: bool = False,
) -> requests.Response:
    """Generates a payload to reply to a message received.

    Args:
        chat_id: Chat ID.
        response: Message to be sent to the user.
        parse_mode: Parse mode. Defaults to ``markdown``
        retry: Retry reply in case reply failed because of parsing.

    Returns:
        Response:
        Response class.
    """
    result = _make_request(
        url=BASE_URL + "/sendMessage",
        payload={"chat_id": chat_id, "text": response, "parse_mode": parse_mode},
    )
    # Retry with response as plain text
    if result.status_code == 400 and parse_mode and not retry:
        LOGGER.warning("Retrying response as plain text with no parsing")
        send_message(chat_id=chat_id, response=response, parse_mode=None, retry=True)
    return result


async def poll_for_messages(offset: int) -> None | int:
    """Polls ``api.telegram.org`` for new messages.

    Args:
        offset: Offset in messages to poll.

    Raises:
        BotInUse:
            - When a new polling is initiated using the same token.
        ConnectionError:
            - If unable to connect to the endpoint.

    See Also:
        Swaps ``offset`` value during every iteration to avoid reprocessing messages.
    """
    response = _make_request(
        url=BASE_URL + "/getUpdates",
        payload={"offset": offset, "timeout": 60},
        method=RequestMethods.GET,
    )
    if response.ok:
        results = response.json().get("result", [])
        if not results:
            return None

        last_update_id = offset
        for result in results:
            if payload := result.get("message"):
                await process_request(payload)
            else:
                LOGGER.error("Received empty payload!!")
            last_update_id = result["update_id"]

        return last_update_id + 1

    # Handle errors
    error_data = response.json()
    err_desc = error_data.get("description", "")

    if response.status_code == 409:
        if "webhook" in err_desc.lower():
            raise exceptions.BotWebhookConflict(err_desc)
        raise exceptions.BotInUse(err_desc)
    if response.status_code == 401:
        raise exceptions.BotTokenInvalid(error_data)
    raise ConnectionError(error_data)


async def process_request(payload: Dict[str, int | dict]) -> None:
    """Processes the request via Telegram messages.

    Args:
        payload: Payload as received.
    """
    LOGGER.debug(payload)
    # noinspection not-mapping
    chat = settings.Chat(**{**payload, **payload["chat"], **payload["from"]})
    if not await authenticate(chat):
        LOGGER.warning(payload)
        return
    if not await verify_timeout(chat):
        LOGGER.warning(payload)
        return
    if payload.get("text"):
        chat.message_type = "text"
        await process_text(chat, settings.Text(**payload))
    elif payload.get("voice"):
        chat.message_type = "voice"
        # noinspection not-mapping
        await process_voice(chat, settings.Voice(**payload["voice"]))
    elif payload.get("document"):
        chat.message_type = "document"
        # noinspection not-mapping
        await process_document(chat, settings.Document(**payload["document"]))
    elif payload.get("video"):
        chat.message_type = "video"
        # noinspection not-mapping
        await process_video(chat, settings.Video(**payload["video"]))
    elif payload.get("audio"):
        chat.message_type = "audio"
        # noinspection not-mapping
        await process_audio(chat, settings.Audio(**payload["audio"]))
    elif payload.get("photo"):
        # Matches for compressed images
        chat.message_type = "photo"
        # noinspection not-mapping,not-iterable
        await process_photo(chat, [settings.PhotoFragment(**d) for d in payload["photo"]])
    else:
        reply_to(chat.id, chat.message_id, "Payload type is not allowed.")


def username_is_valid(username: str | None) -> bool:
    """Compares username and returns True if username is allowed."""
    if not username:
        return False
    for user in config.env.bot_users:
        if secrets.compare_digest(user, username):
            return True
    return False


async def authenticate(chat: settings.Chat) -> bool:
    """Authenticates the user with ``userId`` and ``userName``.

    Args:
        chat: Required section of the payload as a Chat object.

    Returns:
        bool:
        Returns a boolean to indicate whether the user is authenticated.
    """
    if chat.is_bot:
        LOGGER.error("Bot request from %s", chat.username)
        send_message(
            chat_id=chat.id,
            response=f"Sorry {chat.first_name}! I can't process requests from bots.",
        )
        return False
    if chat.id not in config.env.bot_chat_ids or not username_is_valid(username=chat.username):
        LOGGER.error("Unauthorized chatID [%d] or userName [%s]", chat.id, chat.username)
        send_message(chat_id=chat.id, response=f"401 Unauthorized user: ({chat.username})")
        return False
    return True


async def verify_timeout(chat: settings.Chat) -> bool:
    """Verifies whether the message was received in the past 60 seconds.

    Args:
        chat: Required section of the payload as a Chat object.

    Returns:
        bool:
        True or False flag to indicate if the request timed out.
    """
    if int(time.time()) - chat.date < 60:
        return True
    if not chat.date:
        return False
    # Convert Unix timestamp to datetime in the specific timezone
    local_dt = datetime.fromtimestamp(chat.date, tz=config.env.tz)
    current_dt = datetime.now(tz=config.env.tz)
    # Format the datetime objects
    request_time = local_dt.strftime("%m-%d-%Y %H:%M:%S")
    processed_time = current_dt.strftime("%m-%d-%Y %H:%M:%S")
    LOGGER.warning("Request timed out [%s] for %s", request_time, chat.username)
    reply_to(
        chat.id,
        chat.message_id,
        f"Request timed out\nRequested: {request_time}\n" f"Processed: {processed_time}",
    )
    return False


async def process_photo(chat: settings.Chat, data_class: List[settings.PhotoFragment]) -> None:
    """Processes a photo input.

    Args:
        chat: Required section of the payload as a Chat object.
        data_class: Required section of the payload as a Photo object.
    """
    LOGGER.info(data_class)
    reply_to(
        chat.id,
        chat.message_id,
        "Image fragments are not supported. If you're sending a compressed image, "
        "please try sending it without compression.",
    )


async def process_audio(chat: settings.Chat, data_class: settings.Audio) -> None:
    """Processes an audio input.

    Args:
        chat: Required section of the payload as a Chat object.
        data_class: Required section of the payload as an Audio object.
    """
    await process_document(chat, data_class)


async def process_video(chat: settings.Chat, data_class: settings.Video) -> None:
    """Processes a video input.

    Args:
        chat: Required section of the payload as a Chat object.
        data_class: Required section of the payload as a Video object.
    """
    await process_document(chat, data_class)


async def process_voice(chat: settings.Chat, data_class: settings.Voice) -> None:
    """Processes the audio file in the payload received after checking for authentication.

    Args:
        chat: Required section of the payload as a Chat object.
        data_class: Required section of the payload as a Voice object.
    """
    assert data_class, "Requested to process voice, but no voice note was received!"
    reply_to(chat.id, chat.message_id, "Audio inputs are not supported at the moment. Please try text input.")


async def process_document(
    chat: settings.Chat, data_class: settings.Document | settings.Audio | settings.Video
) -> None:
    """Processes the document in the payload received after checking for authentication.

    Args:
        chat: Required section of the payload as a Chat object.
        data_class: Required section of the payload as a Document object.
    """
    assert data_class, "Requested to process document, but no document was received!"
    reply_to(chat.id, chat.message_id, "Document inputs are not supported at the moment. Please try text input.")


def get_process_pool() -> str:
    """Get the status text for the process pool."""
    pending = youtube.processor.status()
    txt = f"Total downloads submitted: {youtube.processor.total_submissions}"
    if pending is not None:
        txt += f"\nPending downloads: {pending}"
    return txt


def get_channel() -> str:
    """Get the channel text for telegram interactions."""
    if config.telegram_beat.poll_for_messages:
        txt = "Channel: Polling"
    elif config.env.bot_webhook:
        txt = f"Channel: Webhook via {config.env.bot_webhook}"
        if config.env.bot_webhook_ip:
            txt += f" - [{config.env.bot_webhook_ip}]"
    else:
        txt = "Channel: Unknown"
    return txt


async def process_text(chat: settings.Chat, data_class: settings.Text) -> None:
    """Processes the text in the payload received after checking for authentication.

    Args:
        chat: Required section of the payload as a Chat object.
        data_class: Required section of the payload as a Text object.
    """
    if data_class.text:
        data_class.text = data_class.text.strip()
    else:
        send_message(chat_id=chat.id, response="Un-processable payload")
        return
    if data_class.text in (Commands.start, Commands.help):
        send_message(chat.id, intro())
        return
    # "status", "stats", "test"
    if data_class.text == Commands.status:
        txt = get_channel()
        try:
            txt += tracker.stringified_get()
        except Exception as error:
            LOGGER.exception(error)
            txt += "\n\n*Trackers:* Failed to get trackers.\n"
        final = f"🕐 *Server Timestamp:* `{config.now()}`\n\n{txt}\n\n{get_process_pool()}"
        reply_to(chat.id, chat.message_id, final)
        return
    try:
        await executor(data_class.text, chat)
    except Exception as error:
        LOGGER.exception(error)
        reply_to(chat.id, chat.message_id, f"❌ *Error*\n\n`{error}`")


async def executor(command: str, chat: settings.Chat) -> None:
    """Executes the command via offline communicator.

    Args:
        command: Command to be executed.
        chat: Required section of the payload as a Chat object.
    """
    LOGGER.info("Request: %s", command)
    # TODO: Write unit tests and code coverage pipeline in GHA
    if command.startswith((Commands.audio, Commands.video)):
        if url := command.replace(Commands.audio, "").replace(Commands.video, "").strip():
            try:
                await asyncio.wait_for(
                    youtube.queue_download(
                        url=HttpUrl(url),
                        source_system=checkpoint.SourceSystem(
                            telegram=chat, audio_only=command.startswith(Commands.audio)
                        ),
                        chat_id=chat.id,
                        message_id=chat.message_id,
                        callback=reply_to,
                    ),
                    timeout=config.env.response_timeout,
                )
            except (asyncio.TimeoutError, DownloadError, ValidationError, AssertionError, ValueError) as error:
                if isinstance(error, asyncio.TimeoutError):
                    LOGGER.warning("Request timed out")
                    response = (
                        "❌ *Metadata lookup failed*\n\n"
                        f"Failed to retrieve metadata within {config.env.response_timeout} seconds.\n\n"
                        "Please try a different URL for this content."
                    )
                else:
                    LOGGER.error(error)
                    response = error.__str__()
                reply_to(chat.id, chat.message_id, response)
        else:
            reply_to(
                chat.id,
                chat.message_id,
                f"❌ *Invalid entry*\n\nURL is required.\n\nUsage: `{Commands.audio} <url>`",
            )
    elif command.startswith(Commands.track):
        invalid_msg = (
            "❌ *Invalid entry*\n\n{pretext}A playlist URL is required, "
            f"followed by `{Commands.track}`\n\n"
            f"Optionally you can also add a schedule with one of {list(config.AllowedCronSchedule.__members__)}"
        )
        if (statement := command.replace(Commands.track, "").strip()).startswith("http"):
            payload = statement.split()
            LOGGER.info(payload)
            try:
                if len(payload) == 1:
                    url = HttpUrl(payload[0])
                    schedule = config.AllowedCronSchedule.DAILY
                elif len(payload) == 2:
                    url = HttpUrl(payload[0])
                    schedule = getattr(config.AllowedCronSchedule, payload[1].upper())
                else:
                    reply_to(chat.id, chat.message_id, invalid_msg.format(pretext=""))
                    return
            except (AttributeError, ValidationError) as error:
                reply_to(chat.id, chat.message_id, invalid_msg.format(pretext=f"{error}\n\n"))
            else:
                response = str(tracker.insert(url, schedule, chat.id))
                reply_to(chat.id, chat.message_id, response)
        else:
            reply_to(chat.id, chat.message_id, invalid_msg.format(pretext=""))
    elif command.startswith(Commands.sync):
        if identifier := command.replace(Commands.sync, "").strip():
            if identifier.startswith("http"):
                await tracker.sync(chat=chat, url=identifier, callback=reply_to)
            else:
                await tracker.sync(chat=chat, name=identifier, callback=reply_to)
        else:
            reply_to(
                chat.id,
                chat.message_id,
                f"❌ *Invalid entry*\n\nPlaylist name [OR] url is required, followed by `{Commands.sync}`.",
            )
    elif command.startswith(Commands.delete):
        if identifier := command.replace(Commands.delete, "").strip():
            if identifier.startswith("http"):
                resp = tracker.delete(url=identifier)
            else:
                resp = tracker.delete(name=identifier)
            reply_to(chat.id, chat.message_id, str(resp))
        else:
            reply_to(
                chat.id,
                chat.message_id,
                f"❌ *Invalid entry*\n\nPlaylist name [OR] url is required, followed by `{Commands.delete}`.",
            )
    else:
        send_message(
            chat_id=chat.id,
            response=f"❌ *Invalid command*\n\n" f"Received: `{command}`\n\n" f"{get_help()}",
        )
