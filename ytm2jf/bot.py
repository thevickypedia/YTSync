# noinspection PyUnresolvedReferences
"""Module for TelegramAPI.

>>> Bot

"""

import secrets
import sys
import time
from enum import StrEnum
from typing import Dict, List

import requests

from ytm2jf.config import env
from ytm2jf.exceptions import BotInUse, BotTokenInvalid, BotWebhookConflict
from ytm2jf.logger import LOGGER
from ytm2jf.settings import Audio, Chat, Document, PhotoFragment, Text, Video, Voice
from ytm2jf.word_match import word_match

BASE_URL = f"https://api.telegram.org/bot{env.bot_token}"


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
    return "\nTo start, send a link to YT music playlist.\n"


def _make_request(
    url: str,
    payload: dict,
    files: dict = None,
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
    chat: Chat,
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


def poll_for_messages(offset: int) -> None | int:
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
                process_request(payload)
            else:
                LOGGER.error("Received empty payload!!")
            last_update_id = result["update_id"]

        return last_update_id + 1

    # Handle errors
    error_data = response.json()
    err_desc = error_data.get("description", "")

    if response.status_code == 409:
        if "webhook" in err_desc.lower():
            raise BotWebhookConflict(err_desc)
        raise BotInUse(err_desc)
    if response.status_code == 401:
        raise BotTokenInvalid(error_data)
    raise ConnectionError(error_data)


def process_request(payload: Dict[str, int | dict]) -> None:
    """Processes the request via Telegram messages.

    Args:
        payload: Payload as received.
    """
    LOGGER.debug(payload)
    chat = Chat(**{**payload, **payload["chat"], **payload["from"]})
    if not authenticate(chat):
        LOGGER.warning(payload)
        return
    if not verify_timeout(chat):
        LOGGER.warning(payload)
        return
    if payload.get("text"):
        chat.message_type = "text"
        process_text(chat, Text(**payload))
    elif payload.get("voice"):
        chat.message_type = "voice"
        process_voice(chat, Voice(**payload["voice"]))
    elif payload.get("document"):
        chat.message_type = "document"
        process_document(chat, Document(**payload["document"]))
    elif payload.get("video"):
        chat.message_type = "video"
        process_video(chat, Video(**payload["video"]))
    elif payload.get("audio"):
        chat.message_type = "audio"
        process_audio(chat, Audio(**payload["audio"]))
    elif payload.get("photo"):
        # Matches for compressed images
        chat.message_type = "photo"
        process_photo(chat, [PhotoFragment(**d) for d in payload["photo"]])
    else:
        reply_to(chat, "Payload type is not allowed.")


def username_is_valid(username: str) -> bool:
    """Compares username and returns True if username is allowed."""
    for user in env.bot_users:
        if secrets.compare_digest(user, username):
            return True
    return False


def authenticate(chat: Chat) -> bool:
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
    if chat.id not in env.bot_chat_ids or not username_is_valid(username=chat.username):
        LOGGER.error(
            "Unauthorized chatID [%d] or userName [%s]", chat.id, chat.username
        )
        send_message(
            chat_id=chat.id, response=f"401 Unauthorized user: ({chat.username})"
        )
        return False
    return True


def verify_timeout(chat: Chat) -> bool:
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


def process_photo(chat: Chat, data_class: List[PhotoFragment]) -> None:
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


def process_audio(chat: Chat, data_class: Audio) -> None:
    """Processes an audio input.

    Args:
        chat: Required section of the payload as Chat object.
        data_class: Required section of the payload as Voice object.
    """
    process_document(chat, data_class)


def process_video(chat: Chat, data_class: Video) -> None:
    """Processes a video input.

    Args:
        chat: Required section of the payload as Chat object.
        data_class: Required section of the payload as Voice object.
    """
    process_document(chat, data_class)


def process_voice(chat: Chat, data_class: Voice) -> None:
    """Processes the audio file in payload received after checking for authentication.

    Args:
        chat: Required section of the payload as Chat object.
        data_class: Required section of the payload as Voice object.
    """
    reply_to(
        chat, "Audio inputs are not supported at the moment. Please try text input."
    )


def process_document(chat: Chat, data_class: Document | Audio | Video) -> None:
    """Processes the document in payload received after checking for authentication.

    Args:
        chat: Required section of the payload as Chat object.
        data_class: Required section of the payload as Document object.
    """
    reply_to(
        chat, "Document inputs are not supported at the moment. Please try text input."
    )


def process_text(chat: Chat, data_class: Text) -> None:
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
    data_class.text = data_class.text.replace("override", "").replace("OVERRIDE", "")
    text_lower = data_class.text.lower()
    if word_match(
        phrase=text_lower,
        match_list=(
            "hey",
            "hola",
            "what's up",
            "ssup",
            "whats up",
            "hello",
            "hi",
            "howdy",
            "hey",
            "chao",
            "hiya",
            "aloha",
        ),
        strict=True,
    ):
        reply_to(
            chat,
            intro(),
        )
        return
    if text_lower == "start":
        send_message(chat.id, intro())
        return
    if text_lower == "help":
        send_message(
            chat_id=chat.id,
            response="Help message here",
        )
        return
    executor(data_class.text, chat)


def executor(command: str, chat: Chat) -> None:
    """Executes the command via offline communicator.

    Args:
        command: Command to be executed.
        chat: Required section of the payload as Chat object.
    """
    LOGGER.info("Request: %s", command)
    response = command  # TODO: Change response to a valid one
    LOGGER.info("Response: %s", response)
    process_response(response, chat)


def process_response(response: str, chat: Chat) -> None:
    """Processes the response via Telegram API.

    Args:
        response: Response from YTM2JF.
        chat: Required section of the payload as Chat object.
    """
    send_message(chat.id, response, None)
