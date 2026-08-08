import os
import socket
from ipaddress import IPv4Address
from typing import List

from pydantic import Field, FilePath, HttpUrl, PositiveInt

from ytm2jf.pydantic_config import PydanticEnvConfig

SECRETS_PATH = (
    os.environ.get("SECRETS_PATH") or os.environ.get("secrets_path") or ".env"
)


class EnvConfig(PydanticEnvConfig):
    """Congiruation values for the project.

    >>> EnvConfig

    """

    host: str = socket.gethostbyname("localhost")
    port: PositiveInt = 4483

    # Telegram config
    bot_token: str
    bot_chat_ids: List[int] = []
    bot_users: List[str] = []

    # Telegram Webhook specific
    bot_webhook: HttpUrl | None = None
    bot_webhook_ip: IPv4Address | None = None
    bot_endpoint: str = Field("/telegram-webhook", pattern=r"^\/")
    bot_secret: str | None = Field(None, pattern="^[A-Za-z0-9_-]{1,256}$")
    bot_certificate: FilePath | None = None

    class Config:
        """Environment variables configuration."""

        vault_table = "ytm2jf"
        env_file = SECRETS_PATH
        extra = "ignore"


env = EnvConfig()
