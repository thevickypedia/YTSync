import os
import socket
from ipaddress import IPv4Address
from typing import List

from pydantic import Field, FilePath, HttpUrl, PositiveFloat, PositiveInt

from yt2jf.pydantic_config import PydanticEnvConfig

SECRETS_PATH = (
    os.environ.get("SECRETS_PATH") or os.environ.get("secrets_path") or ".env"
)


class EnvConfig(PydanticEnvConfig):
    """Congiruation values for the project.

    >>> EnvConfig

    """

    host: str = socket.gethostbyname("localhost")
    port: PositiveInt = 4483

    # Applies to both rsync and telegram polling
    max_retries: PositiveInt | PositiveFloat = Field(10, le=30, ge=1)
    backoff_factor: PositiveInt | PositiveFloat = Field(3, le=10, ge=1)

    # Telegram config
    bot_token: str
    bot_chat_ids: List[int]
    bot_users: List[str]
    poll_interval: PositiveInt | PositiveFloat = Field(2, le=5, ge=1)

    # Remote config
    remote_host: str | None = None
    remote_user: str | None = None
    remote_path: str | None = None
    delete_after_sync: bool = True

    # Telegram Webhook specific
    bot_webhook: HttpUrl | None = None
    bot_webhook_ip: IPv4Address | None = None
    bot_endpoint: str = Field("/telegram-webhook", pattern=r"^\/")
    bot_secret: str | None = Field(None, pattern="^[A-Za-z0-9_-]{1,256}$")
    bot_certificate: FilePath | None = None

    class Config:
        """Environment variables configuration."""

        vault_table = "yt2jf"
        env_file = SECRETS_PATH
        extra = "ignore"


env = EnvConfig()
