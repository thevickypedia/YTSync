import logging
import os
import pathlib
import shlex
import subprocess
import time

from ytsync.config import env

LOGGER = logging.getLogger("ytsync")


class Rsync:
    """Rsync object to copy individual files to remote server.

    >>> Rsync

    """

    def __init__(self):
        """Instantiates the object."""
        self.remote_host = env.remote_host
        self.remote_user = env.remote_user
        self.remote_path = env.remote_path
        self.is_enabled = all((self.remote_host, self.remote_user, self.remote_path, self._is_installed()))

    @staticmethod
    def _is_installed() -> bool:
        """Returns a boolean flag to indicate the rsync installation status."""
        result = subprocess.run(
            ["command", "-V", "rsync"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=True,
        )
        return result.returncode == 0

    def get_remote_path(self, local_path: pathlib.Path | str) -> str:
        """Use existing local filepath to derive the filepath in remote server.

        Args:
            local_path: Local filepath.

        Returns:
            str:
            Filepath in the remote server.
        """
        relative_path = os.path.relpath(local_path, env.data_dir)
        remote_path = os.path.join(self.remote_path, relative_path)
        LOGGER.info("local path: %s -> remote path: %s", local_path, remote_path)
        return remote_path

    # TODO: This solution needs to account multiple paths and have built-in exponential back off
    def remote_file_exists(self, local_path: pathlib.Path) -> bool:
        """Return True if filename exists on the remote server."""
        remote_path = self.get_remote_path(local_path)
        remote_command = f"test -f {shlex.quote(remote_path)}"
        command = [
            "ssh",
            f"{self.remote_user}@{self.remote_host}",
            remote_command,
        ]
        LOGGER.debug("Command: %r", command)
        result = subprocess.run(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=3,
        )
        LOGGER.debug("Return code: ", result.returncode)
        return result.returncode == 0

    def run(self, source: str) -> None:
        """Syncs a file to remote server with exponential backoff retry logic."""
        destination = self.get_remote_path(source)
        remote_location = f"{self.remote_user}@{self.remote_host}:" f"{destination}"
        LOGGER.info("Syncing: '%s' -> '%s'", source, remote_location)

        cmd = [
            "rsync",
            "-avzi",
            "--protect-args",
            "--mkpath",
            "--partial",
            "-e",
            "ssh -o StrictHostKeyChecking=no",
            source,
            remote_location,
        ]

        attempt = 0
        while attempt < env.max_retries:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True)

                if result.returncode == 0:
                    LOGGER.info(f"✅ Successfully synced {source}")
                    return  # Success, exit function

                # Sync failed
                attempt += 1
                if attempt < env.max_retries:
                    # Calculate exponential backoff: 3s, 6s, 12s, 24s...
                    delay = env.backoff_factor * (2 ** (attempt - 1))
                    LOGGER.warning(f"⚠️  Sync failed (Attempt {attempt}/{env.max_retries}). Retrying in {delay}s...")
                    LOGGER.warning(f"   Error: {result.stderr.strip()}")
                    time.sleep(delay)
                else:
                    LOGGER.error(f"❌ Failed to sync {source} after {env.max_retries} attempts.")
                    LOGGER.error(f"   Final Error: {result.stderr.strip()}")
                    raise RuntimeError(f"Transfer Error: {result.stderr.strip()}") from None
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
                    LOGGER.error(f"❌ Failed to sync {source} after {env.max_retries} attempts due to exception.")
                    LOGGER.error(f"   Final Error: {e}")
                    raise RuntimeError(f"Transfer Error [{type(e).__name__}]: {e}") from None
