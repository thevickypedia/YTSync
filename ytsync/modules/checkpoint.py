import pathlib
from ipaddress import IPv4Address
from typing import List

from pydantic import BaseModel, HttpUrl

from ytsync.modules import config, settings


class APISource(BaseModel):
    """Source for API checkpoint."""

    host: str | IPv4Address
    host_header: str | HttpUrl | None = None


class SourceSystem(BaseModel):
    """Source system for checkpoint."""

    api: APISource | None = None
    telegram: settings.Chat | None = None
    scheduled: config.AllowedCronSchedule | None = None
    audio_only: bool = True


class PreFlight(BaseModel):
    """Pre-flight model."""

    total: int = 0
    error: int = 0
    available: int = 0
    unavailable: int = 0


class Checkpoint(BaseModel):
    """Checkpoint model."""

    # Can be determined before the download starts
    source_system: SourceSystem
    input_url: HttpUrl
    resolved_urls: List[HttpUrl]
    is_playlist: bool
    name: str
    initial_destination: pathlib.Path
    final_destination: pathlib.Path
    # Only applies for playlists
    preflight: PreFlight | None = None
    # Awaits download/transfer
    downloaded: List[str] = []
    download_failed: List[str] = []
    transferred: List[str] = []
    transfer_failed: List[str] = []
    runtime: float = 0.0
    playlist_id: str | None = None
    download_start: str = ""
    download_end: str = ""
