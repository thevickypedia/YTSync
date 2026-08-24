import asyncio
import logging
from datetime import datetime

from ytsync.crontab import expression
from ytsync.database import tracker
from ytsync.youtube import youtube

LOGGER = logging.getLogger("ytsync")
LAST_CHECK: datetime | None = None


async def executor() -> None:
    """Executes in a loop to read the database and execute the YouTube sync for the requested URL."""
    global LAST_CHECK
    while True:
        await asyncio.sleep(30)
        now = datetime.now().replace(second=0, microsecond=0)
        if now == LAST_CHECK:
            continue
        LAST_CHECK = now
        for track in tracker.get():
            # Since check_trigger() is true for the whole minute, the last_check guard handles the twice-per-minute case
            # schedule.value is used ONLY here, all inbound and outbound requests follow schedule.name for user-friendly
            if expression.CronExpression(track.schedule.value).check_trigger():
                url = str(track.url)
                LOGGER.info("Executing sync for '%s' with '%s'", track.name, url)
                # TODO: Implement alternate notifications or just pass chat into this
                asyncio.create_task(youtube.queue_download(playlist_url=url))
