import json
import os
import pathlib
import re
from ipaddress import IPv4Address
from typing import Dict, List

from pydantic import BaseModel, HttpUrl

from ytsync.modules import config
from ytsync.telegram import models


class APISource(BaseModel):
    """Source for API checkpoint."""

    host: str | IPv4Address
    host_header: str | HttpUrl | None = None


class SourceSystem(BaseModel):
    """Source system for checkpoint."""

    api: APISource | None = None
    telegram: models.Chat | None = None
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
    # Only applies for playlists; defaults to 0 if not a playlist
    preflight: PreFlight
    # Awaits download/transfer
    downloaded: List[str] = []
    download_failed: List[str] = []
    transferred: List[str] = []
    transfer_failed: List[str] = []
    runtime: float = 0.0
    playlist_id: str | None = None
    download_start: str = ""
    download_end: str = ""


def ls() -> Dict[str, List[int]]:
    """List all the available checkpoints.

    Returns:
        Dict[str, List[int]]:
        A dictionary with datestamps as keys and timestamps as values.
    """
    return {
        parent.name: [
            int(match.group())
            for child in parent.iterdir()
            if child.suffix == ".json"
            if (match := re.search(r"\d+", child.name))
        ]
        for parent in config.checkpoints_dir.iterdir()
        if parent.is_dir() and config.is_valid_checkpoint_dir(parent.name)
    }


def get(datestamp: str, timestamp: int) -> Checkpoint:
    """Get a specific checkpoint.

    Args:
        datestamp: Datestamp of the checkpoint. Example: Aug_29_2026 (directory name)
        timestamp: Timestamp of the checkpoint. Example: 1788010080 (file name identifier)

    Returns:
        Checkpoint:
        The checkpoint object.
    """
    target = config.checkpoints_dir / datestamp / f"checkpoint_{timestamp}.json"
    with open(target) as file:
        data = json.load(file)
    return Checkpoint(**data)


def delete(datestamp: str, timestamp: int) -> None:
    """Delete a specific checkpoint.

    Args:
        datestamp: Datestamp of the checkpoint. Example: Aug_29_2026 (directory name)
        timestamp: Timestamp of the checkpoint. Example: 1788010080 (file name identifier)
    """
    directory = config.checkpoints_dir / datestamp
    # If it's a single file, delete the directory
    if len([file for file in directory.iterdir() if file.suffix == ".json"]) == 1:
        directory.rmdir()
    else:
        target = directory / f"checkpoint_{timestamp}.json"
        os.remove(target)
