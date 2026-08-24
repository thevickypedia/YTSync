import asyncio
import logging

from ytsync.crontab import expression
from ytsync.database import tracker
from ytsync.youtube import youtube

LOGGER = logging.getLogger("ytsync")


async def executor() -> None:
    """Executes in a loop to read the database and execute the YouTube sync for the requested URL."""
    while True:
        await asyncio.sleep(30)
        for track in tracker.get():
            if expression.CronExpression(track.schedule).check_trigger():
                LOGGER.info("Executing sync for '%s' with '%s'", track.name, track.url)
                # TODO: Implement alternate notifications or just pass chat into this
                asyncio.create_task(youtube.queue_download(playlist_url=track.url))
