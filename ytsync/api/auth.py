import logging
import secrets
from http import HTTPStatus

from fastapi import Request
from fastapi.exceptions import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from ytsync.modules import config

LOGGER = logging.getLogger("ytsync")


async def validate_bot_request(bot_token: HTTPAuthorizationCredentials) -> None:
    """Function to authenticate inbound requests.

    Args:
        bot_token: Bot token to validate an ingress request for webhook operations.
    """
    if not secrets.compare_digest(bot_token.credentials, config.env.bot_token):
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED.real,
        )


async def validate_api_request(apikey: HTTPAuthorizationCredentials) -> config.Profile:
    """Function to authenticate inbound requests.

    Args:
        apikey: API key to validate an ingress request.
    """
    for profile in config.env.profiles:
        if secrets.compare_digest(apikey.credentials, profile.apikey):
            return profile
    else:
        raise HTTPException(
            status_code=HTTPStatus.UNAUTHORIZED.real,
        )


async def two_factor(request: Request) -> bool:
    """Two-factor verification for messages coming via webhook.

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
