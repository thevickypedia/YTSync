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
from typing import Callable, Dict, List

import requests
from yt_dlp.utils import DownloadError

from ytsync.database import tracker
from ytsync.modules import config, exceptions, settings
from ytsync.youtube import youtube

BASE_URL = f"https://api.telegram.org/bot{config.env.bot_token}"
LOGGER = logging.getLogger("ytsync")
ACTIVE_TASKS: Dict[str, asyncio.Task] = {}


class RequestMethods(StrEnum):
    """Allowed request methods.

    >>> RequestMethods

    """

    GET = "GET"
    POST = "POST"


def intro() -> str:
    """Returns a welcome message as a string.

    Returns:
        str:
    """
    return (
        "\nTo start, send a link to YT music playlist in the following format:\n\n"
        "- /id: <playlist id>\n- /url: <playlist url>\n"
    )


def _make_request(
    url: str,
    payload: dict,
    files: dict | None = None,
    method: RequestMethods = RequestMethods.POST,
) -> requests.Response:
    """Makes a post request with a ``connect timeout`` of 5 seconds and ``read timeout`` of 60.

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
    chat: settings.Chat,
    response: str,
    parse_mode: str | None = "markdown",
    retry: bool = False,
) -> requests.Response:
    """Generates a payload to reply to a message received.

    Args:
        chat: Required section of the payload as Chat object.
        response: Message to be sent to the user.
        parse_mode: Parse mode. Defaults to ``markdown``
        retry: Retry reply in case reply failed because of parsing.

    Returns:
        Response:
        Response class.
    """
    result = _make_request(
        url=BASE_URL + "/sendMessage",
        payload={
            "chat_id": chat.id,
            "reply_to_message_id": chat.message_id,
            "text": response,
            "parse_mode": parse_mode,
        },
    )
    # Retry with response as plain text
    if result.status_code == 400 and parse_mode and not retry:
        LOGGER.warning("Retrying response as plain text with no parsing")
        reply_to(chat, response, None, True)
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
        reply_to(chat, "Payload type is not allowed.")


def username_is_valid(username: str) -> bool:
    """Compares username and returns True if username is allowed."""
    for user in config.env.bot_users:
        if secrets.compare_digest(user, username):
            return True
    return False


async def authenticate(chat: settings.Chat) -> bool:
    """Authenticates the user with ``userId`` and ``userName``.

    Args:
        chat: Required section of the payload as Chat object.

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
        chat: Required section of the payload as Chat object.

    Returns:
        bool:
        True or False flag to indicate if the request timed out.
    """
    if int(time.time()) - chat.date < 60:
        return True
    request_time = time.strftime("%m-%d-%Y %H:%M:%S", time.localtime(chat.date))
    LOGGER.warning("Request timed out [%s] for %s", request_time, chat.username)
    reply_to(
        chat,
        f"Request timed out\nRequested: {request_time}\n"
        f"Processed: {time.strftime('%m-%d-%Y %H:%M:%S', time.localtime(time.time()))}",
    )
    return False


async def process_photo(chat: settings.Chat, data_class: List[settings.PhotoFragment]) -> None:
    """Processes a photo input.

    Args:
        chat: Required section of the payload as Chat object.
        data_class: Required section of the payload as Voice object.
    """
    LOGGER.info(data_class)
    reply_to(
        chat,
        "Image fragments are not supported. If you're sending a compressed image, "
        "please try sending it without compression.",
    )


async def process_audio(chat: settings.Chat, data_class: settings.Audio) -> None:
    """Processes an audio input.

    Args:
        chat: Required section of the payload as Chat object.
        data_class: Required section of the payload as Voice object.
    """
    await process_document(chat, data_class)


async def process_video(chat: settings.Chat, data_class: settings.Video) -> None:
    """Processes a video input.

    Args:
        chat: Required section of the payload as Chat object.
        data_class: Required section of the payload as Voice object.
    """
    await process_document(chat, data_class)


async def process_voice(chat: settings.Chat, data_class: settings.Voice) -> None:
    """Processes the audio file in payload received after checking for authentication.

    Args:
        chat: Required section of the payload as Chat object.
        data_class: Required section of the payload as Voice object.
    """
    assert data_class, "Requested to process voice, but no voice note was received!"
    reply_to(chat, "Audio inputs are not supported at the moment. Please try text input.")


async def process_document(
    chat: settings.Chat, data_class: settings.Document | settings.Audio | settings.Video
) -> None:
    """Processes the document in payload received after checking for authentication.

    Args:
        chat: Required section of the payload as Chat object.
        data_class: Required section of the payload as Document object.
    """
    assert data_class, "Requested to process document, but no document was received!"
    reply_to(chat, "Document inputs are not supported at the moment. Please try text input.")


def trackers_text() -> str:
    """Get trackers in a Markdown friendly format."""
    txt = ""
    if trackers := tracker.get():
        txt += "\n\n*Trackers:*\n"
        for idx, tracked in enumerate(trackers, start=1):
            url, name, schedule = tracked
            txt += f"{idx}. *{name}* — `{schedule}`\n"
    else:
        LOGGER.info("No trackers found.")
    return txt


async def process_text(chat: settings.Chat, data_class: settings.Text) -> None:
    """Processes the text in payload received after checking for authentication.

    Args:
        chat: Required section of the payload as Chat object.
        data_class: Required section of the payload as Text object.
    """
    if data_class.text:
        data_class.text = data_class.text.strip()
    else:
        send_message(chat_id=chat.id, response="Un-processable payload")
        return
    text_lower = data_class.text.lower().lstrip("/")
    if text_lower == "start":
        send_message(chat.id, intro())
        return
    if text_lower == "help":
        send_message(
            chat_id=chat.id,
            response="Use '/id' or '/url' followed by the identifier.",
        )
        return
    if text_lower in ("status", "stats", "test"):
        task = ACTIVE_TASKS.get("poll")
        if task and not task.done():
            txt = "Channel: Polling"
        elif config.env.bot_webhook:
            txt = f"Channel: Webhook via {config.env.bot_webhook}"
            if config.env.bot_webhook_ip:
                txt += f" - [{config.env.bot_webhook_ip}]"
        else:
            txt = "Channel: Unknown"
        txt += trackers_text()
        now = datetime.now()
        tzname = now.astimezone().tzname() or ""
        final = f"🕐 *Server Timestamp:* `{now.strftime('%c')} {tzname}`\n\n{txt}"
        reply_to(chat, final)
        return
    try:
        await executor(data_class.text, chat)
    except Exception as error:
        reply_to(chat, f"❌ *Error*\n\n`{error}`")


async def executor(command: str, chat: settings.Chat) -> None:
    """Executes the command via offline communicator.

    Args:
        command: Command to be executed.
        chat: Required section of the payload as Chat object.
    """
    LOGGER.info("Request: %s", command)
    # TODO:
    #   Write unit tests and code coverage pipeline in GHA
    #   Feature to allow cookies
    #   Auto-detect video vs audio and change 'options' accordingly (currently all MP3)
    kwargs: Dict[str, str | Callable | settings.Chat] = dict(chat=chat, callback=reply_to)
    if command.startswith("/id"):
        if playlist_id := command.replace("/id", "").strip():
            kwargs["playlist_id"] = playlist_id
        else:
            reply_to(chat, "❌ *Invalid entry*\n\nA playlist ID is required.\n\nUsage: `/id <playlist_id>`")
            return
    elif command.startswith("/url"):
        if playlist_url := command.replace("/url", "").strip():
            kwargs["playlist_url"] = playlist_url
        else:
            reply_to(chat, "❌ *Invalid entry*\n\nA playlist URL is required.\n\nUsage: `/url <playlist_url>`")
            return
    elif command.startswith("/track"):
        if (statement := command.replace("/track", "").strip()).startswith("http"):
            response = str(tracker.insert(statement))
            reply_to(chat, response)
        else:
            reply_to(chat, "❌ *Invalid entry*\n\nA playlist URL is required, followed by `/track`.")
        return
    elif command.startswith("/sync"):
        if index := command.replace("/sync", "").strip():
            if index.isdigit():
                response = tracker.sync(int(index))
                reply_to(chat, response)
            else:
                reply_to(chat, f"❌ *Error*\n\nInvalid index received: {index}{trackers_text()}")
            return
        else:
            reply_to(chat, "❌ *Invalid entry*\n\nAn index ID is required, followed by `/sync`.")
        return
    elif command.startswith("/delete"):
        if index := command.replace("/delete", "").strip():
            if index.isdigit():
                # NOTE: enumeration in trackers_text fn starts at 1, hence the negation here
                response = str(tracker.delete(int(index) - 1))
                reply_to(chat, response)
            else:
                reply_to(chat, f"❌ *Error*\n\nInvalid index received: {index}{trackers_text()}")
            return
        else:
            reply_to(chat, f"❌ *Invalid entry*\n\nAn index ID is required, followed by `/delete`.{trackers_text()}")
        return
    else:
        send_message(
            chat_id=chat.id,
            response=(
                f"❌ *Invalid command*\n\n"
                f"Received: `{command}`\n\n"
                f"Use `/id` or `/url` followed by the identifier."
            ),
        )
        return
    try:
        response = await asyncio.wait_for(youtube.queue_download(**kwargs), timeout=10)
    except (asyncio.TimeoutError, ValueError, AssertionError, DownloadError) as error:
        if isinstance(error, asyncio.TimeoutError):
            LOGGER.warning("Request timed out")
            response = (
                "❌ *Metadata lookup failed*\n\n"
                "Failed to retrieve metadata within 10 seconds.\n\n"
                "Please try a different `/id` or `/url` for this content."
            )
        else:
            LOGGER.error(error)
            response = error.__str__()
    reply_to(chat, response)
