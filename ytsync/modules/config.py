import math
import os
import pathlib
import socket
from datetime import datetime, tzinfo
from enum import StrEnum
from ipaddress import IPv4Address
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

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

    HOURLY = "@hourly"
    DAILY = "@daily"
    WEEKLY = "@weekly"
    MONTHLY = "@monthly"


class EnvConfig(pydantic_config.PydanticEnvConfig):
    """Configuration values for the project.

    >>> EnvConfig

    """

    host: str = socket.gethostbyname("localhost")
    port: PositiveInt = 4483
    tz: ZoneInfo | tzinfo | None = datetime.now().astimezone().tzinfo
    log_config: FilePath | Dict[str, Any] | None = None

    # Applies to rsync and telegram polling
    max_retries: PositiveInt = Field(10, le=30, ge=1)
    backoff_factor: PositiveInt | PositiveFloat = Field(3, le=10, ge=1)

    # Concurrency
    # Maximum number of parallel transfers to remote server
    max_transfers: PositiveInt = Field(PHYSICAL_CORES, le=LOGICAL_CORES, ge=1)

    # Sequential download factors
    # Cooldown period (in seconds) between every download (always sequential)
    delayed_start: bool = False
    # Next available time cannot be accurately determined before it begins
    # 'next_buffer' with # of seconds is used to simulate an actual download duration
    next_buffer: PositiveInt = Field(60, ge=30, le=300)  # 30s to 5m; default: 60s
    # 'cooldown_interval' with # of seconds is used to propagate delay between each download
    cooldown_interval: PositiveInt = Field(300, ge=30, le=10_800)  # 30s to 3h; default: 5m

    # Percentage of errors YTSync needs to tolerate before trying to download the entire playlist
    max_error_threshold: PositiveInt = Field(30, le=100, ge=10)

    # Data
    data_dir: NewPath | DirectoryPath = pathlib.Path("data")
    logs_dir: NewPath | DirectoryPath = pathlib.Path("logs")
    download_dir: NewPath | DirectoryPath = pathlib.Path("downloads")

    # Telegram config
    bot_token: str
    bot_chat_ids: List[int]
    bot_users: List[str]
    poll_interval: PositiveInt = Field(2, le=10, ge=1)
    response_timeout: PositiveInt = Field(30, ge=10, le=60)

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


def tzname() -> str:
    """Returns the timezone name regardless of the TZ value set.

    Converts "America/Chicago" to "CDT"
    """
    return datetime.now(env.tz).tzname() or ""


def now() -> datetime:
    """Returns the datetime object in the current timezone."""
    return datetime.now(env.tz)


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

env.data_dir.mkdir(exist_ok=True)
env.download_dir.mkdir(exist_ok=True)
db = database.Database(database=env.data_dir.joinpath("database.db"))
db.create_table(table_name="ytsync", columns=["url", "name", "schedule"], primary_key="url")
if not env.apikey:
    env.apikey = env.bot_token
