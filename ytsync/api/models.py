from enum import StrEnum
from ipaddress import IPv4Address

from pydantic import BaseModel, HttpUrl

from ytsync.modules import config


class SetWebhook(BaseModel):
    """Request payload for POST webhook endpoint.

    >>> SetWebhook

    """

    webhook: HttpUrl
    secret_token: str
    webhook_ip: IPv4Address | None = None


class Trackers(BaseModel):
    """Payload to add trackers through API.

    >>> Trackers

    """

    url: HttpUrl
    schedule: config.AllowedCronSchedule = config.AllowedCronSchedule.DAILY
    chat_id: int = 0


class DeleteTrackers(BaseModel):
    """Payload to delete trackers through API.

    >>> DeleteTrackers

    """

    name: str | None = None
    url: str | None = None
    chat_id: int = 0


class Download(BaseModel):
    """Payload to sync a track on-demand.

    >>> Download

    """

    url: HttpUrl
    audio_only: bool
    chat_id: int = 0


class Tags(StrEnum):
    """Tags to group endpoints in SwaggerUI.

    >>> Tags

    """

    YTSync = "API route to download an audio or video URL on-demand."
    Webhook = (
        "API routes to get, set or delete telegram webhook.\n"
        "YTSync automatically starts/stops long polling based on the webhook status."
    )
    Tracker = (
        "API routes to get, add or delete trackers.\n"
        "Trackers are used to sync new/existing tracks automatically on a schedule."
    )
    Checkpoint = (
        "API routes to list all checkpoints, get [OR] delete a checkpoint.\n"
        "Checkpoints are stored as JSON files after successful downloads/transfers."
    )
    Queue = (
        "API routes to get, add or delete downloads from the queue.\n"
        "Pending downloads are added to the queue with a timestamp to run."
    )
