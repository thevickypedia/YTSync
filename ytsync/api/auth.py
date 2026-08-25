import logging
import secrets
from http import HTTPStatus

from fastapi import Request
from fastapi.exceptions import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from ytsync.modules import config

LOGGER = logging.getLogger("ytsync")


def validate(apikey: HTTPAuthorizationCredentials, bot_request: bool) -> None:
    """Function to authenticate inbound requests.

    Args:
        apikey: API key to validate an ingress request.
        bot_request: Boolean flag to indicate bot operation.
    """
    secret = config.env.bot_token if bot_request else config.env.apikey
    if not secrets.compare_digest(apikey.credentials, secret):
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED.real,
        )


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
