import logging
from typing import List

from ytsync.crontab import expression
from ytsync.youtube import youtube

LOGGER = logging.getLogger("ytsync")


async def crontab_executor(cron_jobs: List[expression.CronExpression]) -> None:
    """Checks and triggers cron jobs based on their defined schedules.

    Args:
        cron_jobs: List of CronExpression objects representing the cron jobs to be monitored.
    """
    # TODO: Read from the DB every minute and pass it here as list of cron expression objects
    for job in cron_jobs:
        if job.check_trigger():
            LOGGER.debug("Executing cron job: '%s'", job.comment)
            playlist_url = playlist_id = None
            if job.comment.startswith("http"):
                playlist_url = job.comment.strip()
            elif job.comment.strip():
                playlist_id = job.comment.strip()
            # TODO: Callback with ntfy notification
            youtube.queue_download(playlist_id=playlist_id, playlist_url=playlist_url)
