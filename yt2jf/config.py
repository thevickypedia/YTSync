import math
import os
import pathlib
import socket
import warnings
from ipaddress import IPv4Address
from multiprocessing import current_process
from typing import Any, Dict, List

from pydantic import Field, FilePath, HttpUrl, PositiveFloat, PositiveInt, NewPath, DirectoryPath

from yt2jf.pydantic_config import PydanticEnvConfig

SECRETS_PATH = (
    os.environ.get("SECRETS_PATH") or os.environ.get("secrets_path") or ".env"
)
LOGICAL_CORES = os.cpu_count() or 2
PHYSICAL_CORES = math.ceil(LOGICAL_CORES / 2)


class EnvConfig(PydanticEnvConfig):
    """Configuration values for the project.

    >>> EnvConfig

    """

    host: str = socket.gethostbyname("localhost")
    port: PositiveInt = 4483
    log_config: FilePath | Dict[str, Any] | None = None

    # Applies to both rsync and telegram polling
    max_retries: PositiveInt | PositiveFloat = Field(10, le=30, ge=1)
    backoff_factor: PositiveInt | PositiveFloat = Field(3, le=10, ge=1)

    # Concurrency
    max_listeners: PositiveInt = Field(PHYSICAL_CORES, le=LOGICAL_CORES, ge=1)
    max_transfers: PositiveInt = Field(LOGICAL_CORES, le=LOGICAL_CORES * 2, ge=1)

    # Data
    data_dir: NewPath | DirectoryPath = pathlib.Path("data")

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

if (
    not all((env.remote_host, env.remote_path, env.remote_user))
    and current_process().name == "MainProcess"
):
    warnings.warn(
        "No remote connections have been setup, all downloaded media will be stored locally."
    )
env.data_dir.mkdir(exist_ok=True)
