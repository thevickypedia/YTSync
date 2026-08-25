from ipaddress import IPv4Address

from pydantic import BaseModel, HttpUrl

from ytsync.modules import config


class SetWebhook(BaseModel):
    """Request payload for POST webhook endpoint."""

    webhook: HttpUrl
    secret_token: str
    webhook_ip: IPv4Address | None = None


class Trackers(BaseModel):
    """Payload to add trackers through API."""

    url: HttpUrl
    schedule: config.AllowedCronSchedule = config.AllowedCronSchedule.DAILY
    chat_id: int = 0


class DeleteTrackers(BaseModel):
    """Payload to delete trackers through API."""

    name: str | None = None
    url: str | None = None
    chat_id: int = 0


class SyncTrack(BaseModel):
    """Payload to sync a track on-demand."""

    url: HttpUrl
    chat_id: int = 0
