import math
import os
import pathlib
import socket
import warnings
from enum import StrEnum
from ipaddress import IPv4Address
from multiprocessing import current_process
from typing import Any, Dict, List

from pydantic import (
    DirectoryPath,
    Field,
    FilePath,
    HttpUrl,
    NewPath,
    PositiveFloat,
    PositiveInt,
    ValidationError,
)
from pydantic_core import InitErrorDetails

from ytsync.database import database
from ytsync.modules import pydantic_config

SECRETS_PATH = os.environ.get("SECRETS_PATH") or os.environ.get("secrets_path") or ".env"
LOGICAL_CORES = os.cpu_count() or 2
PHYSICAL_CORES = math.ceil(LOGICAL_CORES / 2)
PLAYLIST_URL = "https://music.youtube.com/playlist?list={playlist_id}"


class AllowedCronSchedule(StrEnum):
    """Allowed cron schedule to track playlists."""

    HOURLY = "0 * * * *"
    DAILY = "0 0 * * *"
    WEEKLY = "0 0 * * 0"
    MONTHLY = "0 0 1 * *"


class EnvConfig(pydantic_config.PydanticEnvConfig):
    """Configuration values for the project.

    >>> EnvConfig

    """

    host: str = socket.gethostbyname("localhost")
    port: PositiveInt = 4483
    log_config: FilePath | Dict[str, Any] | None = None

    # Applies to rsync and telegram polling
    max_retries: PositiveInt = Field(10, le=30, ge=1)
    backoff_factor: PositiveInt | PositiveFloat = Field(3, le=10, ge=1)

    # Concurrency
    # Maximum number of parallel transfers to remote server
    max_transfers: PositiveInt = Field(PHYSICAL_CORES, le=LOGICAL_CORES, ge=1)
    # Cooldown period (in seconds) between every download (always sequential)
    cooldown_interval: PositiveInt = Field(300, ge=30, le=10_800)  # 30s to 3h; default: 5m

    # Percentage of errors YTSync needs to tolerate before trying to download the entire playlist
    max_error_threshold: PositiveInt = Field(30, le=100, ge=10)

    # Data
    data_dir: NewPath | DirectoryPath = pathlib.Path("data")
    logs_dir: NewPath | DirectoryPath = pathlib.Path("logs")
    download_dir: NewPath | DirectoryPath = pathlib.Path("downloads")
    database: str | pathlib.Path = Field("database.db", pattern=r"^[A-Za-z0-9]+\.db$")

    # Telegram config
    bot_token: str
    bot_chat_ids: List[int]
    bot_users: List[str]
    poll_interval: PositiveInt | PositiveFloat = Field(2, le=10, ge=1)
    default_tracker: AllowedCronSchedule = AllowedCronSchedule.DAILY

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

    # API config
    apikey: str | None = None

    class Config:
        """Environment variables configuration."""

        vault_table = "ytsync"
        env_file = SECRETS_PATH
        extra = "ignore"


# noinspection argument-list
env = EnvConfig()

# 'bot_webhook' is optional but 'bot_endpoint' is mandatory
# 'bot_endpoint' is registered in FastAPI during startup to serve incoming requests via webhooks
if env.bot_webhook and env.bot_webhook.path != env.bot_endpoint:
    raise ValidationError.from_exception_data(
        title="YTSync",
        line_errors=[
            InitErrorDetails(
                type="value_error",
                loc=("bot_webhook",),
                input="invalid",
                ctx={"error": ValueError("'bot_webhook.path' must match 'bot_endpoint'")},
            ),
        ],
    )
if not all((env.remote_host, env.remote_path, env.remote_user)) and current_process().name == "MainProcess":
    warnings.warn("No remote connections have been setup, all downloaded media will be stored locally.")
env.data_dir.mkdir(exist_ok=True)
env.download_dir.mkdir(exist_ok=True)
env.database = env.data_dir.joinpath(env.database)
db = database.Database(database=env.database)
db.create_table(table_name="ytsync", columns=["url", "name", "schedule"], primary_key="url")
if not env.apikey:
    env.apikey = env.bot_token
