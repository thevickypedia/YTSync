import asyncio
import math
import os
import pathlib
import socket
import warnings
from datetime import datetime, tzinfo
from enum import StrEnum
from ipaddress import IPv4Address
from typing import Any, Dict, List
from zoneinfo import ZoneInfo

import httpx
from pydantic import (
    BaseModel,
    DirectoryPath,
    Field,
    FilePath,
    HttpUrl,
    NewPath,
    PositiveFloat,
    PositiveInt,
    ValidationError,
    field_validator,
)
from pydantic_core import InitErrorDetails

from ytsync.database import database
from ytsync.modules import pydantic_config, settings
from ytsync.version import __version__

SECRETS_PATH = os.environ.get("SECRETS_PATH") or os.environ.get("secrets_path") or ".env"
CONFIG_PATH = os.environ.get("CONFIG_PATH") or os.environ.get("config_path") or ".env.json"
LOGICAL_CORES = os.cpu_count() or 2
PHYSICAL_CORES = math.ceil(LOGICAL_CORES / 2)
YT_FILENAME_TEMPLATE = "%(title)s.%(ext)s"
ASYNC_CLIENT: httpx.AsyncClient
MAIN_EVENT_LOOP: asyncio.AbstractEventLoop


telegram_beat = settings.TelegramBeat()


class API(StrEnum):
    """API information."""

    name = "YTSync"
    version = __version__
    repo_name = "thevickypedia/YTSync"
    repo_link = "https://github.com/thevickypedia/YTSync"
    version_link = f"{repo_link}/releases/tag/v{version}"


class AllowedCronSchedule(StrEnum):
    """Allowed cron schedule to track playlists."""

    HOURLY = "@hourly"
    DAILY = "@daily"
    WEEKLY = "@weekly"
    MONTHLY = "@monthly"


class Profile(BaseModel):
    """Use profile information.

    >>> Profile

    """

    name: str
    # API access
    apikey: str
    # Telegram bot
    bot_username: str
    bot_chat_id: int


def get_profile_by_bot_user(username: str) -> Profile:
    """Get profile by username."""
    for profile in env.profiles:
        if profile.bot_username == username:
            return profile
    else:
        raise ValueError(f"Profile for username {username!r} not found")


class EnvConfig(pydantic_config.PydanticEnvConfig):
    """Configuration values for the project.

    >>> EnvConfig

    """

    # Server config
    host: str = socket.gethostbyname("localhost")
    port: PositiveInt = 4483
    tz: ZoneInfo | tzinfo | None = datetime.now().astimezone().tzinfo
    log_config: FilePath | Dict[str, Any] | None = None

    # Profiles
    profiles: List[Profile]

    # Telegram config
    bot_token: str
    poll_interval: PositiveInt = Field(2, le=10, ge=1)

    bot_webhook: HttpUrl | None = None
    bot_webhook_ip: IPv4Address | None = None
    bot_endpoint: str = Field("/telegram-webhook", pattern=r"^\/")
    bot_secret: str | None = Field(None, pattern="^[A-Za-z0-9_-]{1,256}$")
    bot_certificate: FilePath | None = None

    # yt-dlp config
    download_tester: bool = False
    # https://github.com/yt-dlp/yt-dlp/wiki/FAQ#how-do-i-pass-cookies-to-yt-dlp
    # https://github.com/yt-dlp/yt-dlp/wiki/FAQ#http-error-429-too-many-requests-or-402-payment-required
    cookie_file: FilePath | None = None
    source_address: IPv4Address | None = None
    proxy_url: HttpUrl | None = None

    # FileIO config
    data_dir: NewPath | DirectoryPath = pathlib.Path("data")
    logs_dir: NewPath | DirectoryPath = pathlib.Path("logs")
    audio_dir: NewPath | DirectoryPath = pathlib.Path("audio")
    video_dir: NewPath | DirectoryPath = pathlib.Path("video")

    # Maximum number of parallel transfers to remote server
    max_transfers: PositiveInt = Field(PHYSICAL_CORES, le=LOGICAL_CORES, ge=1)
    # Applies to rsync and telegram polling
    max_retries: PositiveInt = Field(10, le=30, ge=1)
    max_timeout: PositiveInt = Field(60, le=300, ge=5)
    backoff_factor: PositiveInt | PositiveFloat = Field(3, le=10, ge=1)
    # Percentage of errors YTSync needs to tolerate before trying to download the base url
    max_error_threshold: PositiveInt = Field(30, le=100, ge=10)
    response_timeout: PositiveInt = Field(30, ge=10, le=60)

    # Sequential download factors
    delayed_start: bool = False
    # Next available time cannot be accurately determined before it begins
    # 'next_buffer' with # of seconds is used to simulate an actual download duration
    next_buffer: PositiveInt = Field(60, ge=30, le=300)  # 30s to 5m; default: 60s
    # 'cooldown_interval' with # of seconds is used to propagate delay between each download
    cooldown_interval: PositiveInt = Field(300, ge=60, le=10_800)  # 60s to 3h; default: 5m

    # Cleanup checkpoints
    checkpoint_retention: str = Field("3d")

    # Remote config
    remote_host: str | None = None
    remote_user: str | None = None
    remote_path: str | None = None
    delete_after_sync: bool = True

    @field_validator("checkpoint_retention")
    @classmethod
    def validate_retention_period(cls, value: str) -> str:
        """Validates the retention period."""
        return settings.validate_retention_period(value)

    class Config:
        """Environment variables configuration."""

        vault_table = "ytsync"
        env_file = SECRETS_PATH
        config_file = CONFIG_PATH
        extra = "ignore"


# noinspection argument-list
env = EnvConfig()


def tzname() -> str:
    """Returns the timezone name regardless of the TZ value set.

    Converts "America/Chicago" to "CDT"
    """
    return datetime.now(env.tz).tzname() or ""


def now() -> str:
    """Returns the datetime object in the current timezone."""
    return datetime.now(env.tz).strftime("%a %b %d %Y %H:%M %Z")


# 'bot_webhook' is optional, but 'bot_endpoint' is mandatory
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

env.data_dir.mkdir(exist_ok=True, parents=True)
env.audio_dir.mkdir(exist_ok=True, parents=True)
env.video_dir.mkdir(exist_ok=True, parents=True)
db = database.Database(database=env.data_dir.joinpath("database.db"))
db.create_table(table_name="ytsync", columns=["profile_name", "url", "name", "schedule", "chat_id"])
db.create_table(table_name="queue", columns=["profile_name", "timestamp", "data"], primary_key="timestamp")
checkpoints_dir = env.data_dir / "checkpoints"
checkpoints_dir.mkdir(exist_ok=True, parents=True)
checkpoint_dir_format = "%b_%d_%Y"
# Raise a warning if download tester is enabled
if env.download_tester:
    warnings.warn("Download tester is enabled. This is not recommended for production use.", UserWarning, stacklevel=2)


def is_valid_checkpoint_dir(value):
    """Checks if a string is a valid checkpoint directory name."""
    try:
        datetime.strptime(value, checkpoint_dir_format)
        return True
    except ValueError:
        return False
