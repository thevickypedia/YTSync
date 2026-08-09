import logging
import os
import shlex
import subprocess
import time

from yt2jf.config import env

LOGGER = logging.getLogger("uvicorn.default")


class Rsync:
    """Rsync object to copy individual files to remote server.

    >>> Rsync

    """

    def __init__(self):
        """Instantiates the object."""
        self.remote_host = env.remote_host
        self.remote_user = env.remote_user
        self.remote_path = env.remote_path
        self.is_enabled = all(
            (self.remote_host, self.remote_user, self.remote_path, self._is_installed())
        )

    def _is_installed(self) -> bool:
        """Returns a boolean flag to indicate the rsync installation status."""
        result = subprocess.run(
            ["command", "-V", "rsync"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=True,
        )
        return result.returncode == 0

    def run(self, filepath) -> None:
        """Syncs a file to remote server with exponential backoff retry logic."""
        local_root = str(env.data_dir)
        relative_path = os.path.relpath(filepath, local_root)
        remote_location = (
            f"{self.remote_user}@{self.remote_host}:"
            f"{shlex.quote(os.path.join(self.remote_path, relative_path))}"
        )
        LOGGER.info("Syncing: '%s' -> '%s'", filepath, remote_location)

        cmd = [
            "rsync",
            "-avz",  # <- enable archive mode
            "--relative",  # <-- maintain relative path in the remote server
            "--partial",  # <- keep partially transferred files if a transfer is interrupted
            "-e",  # <- specify remote shell program; followed by ssh
            "ssh -o StrictHostKeyChecking=no",
            filepath,
            remote_location,
        ]

        attempt = 0
        while attempt < env.max_retries:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True)

                if result.returncode == 0:
                    LOGGER.info(f"✅ Successfully synced {filepath}")
                    return  # Success, exit function

                # Sync failed
                attempt += 1
                if attempt < env.max_retries:
                    # Calculate exponential backoff: 3s, 6s, 12s, 24s...
                    delay = env.backoff_factor * (2 ** (attempt - 1))
                    LOGGER.warning(
                        f"⚠️  Sync failed (Attempt {attempt}/{env.max_retries}). Retrying in {delay}s..."
                    )
                    LOGGER.warning(f"   Error: {result.stderr.strip()}")
                    time.sleep(delay)
                else:
                    LOGGER.error(
                        f"❌ Failed to sync {filepath} after {env.max_retries} attempts."
                    )
                    LOGGER.error(f"   Final Error: {result.stderr.strip()}")
            except Exception as e:
                attempt += 1
                if attempt < env.max_retries:
                    delay = env.backoff_factor * (2 ** (attempt - 1))
                    LOGGER.warning(
                        f"⚠️  Exception occurred (Attempt {attempt}/{env.max_retries}). Retrying in {delay}s..."
                    )
                    LOGGER.warning(f"   Error: {e}")
                    time.sleep(delay)
                else:
                    LOGGER.error(
                        f"❌ Failed to sync {filepath} after {env.max_retries} attempts due to exception."
                    )
                    LOGGER.error(f"   Final Error: {e}")
