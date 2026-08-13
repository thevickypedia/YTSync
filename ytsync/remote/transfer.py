import logging
import os
import pathlib
import posixpath
import shlex
import subprocess
import time
from typing import List, Set

from ytsync.modules import config

LOGGER = logging.getLogger("ytsync")


class Rsync:
    """Rsync object to copy individual files to remote server.

    >>> Rsync

    """

    def __init__(self):
        """Instantiates the object."""
        self.remote_host = config.env.remote_host
        self.remote_user = config.env.remote_user
        self.remote_path = config.env.remote_path
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
        relative_path = os.path.relpath(local_path, config.env.data_dir)
        return posixpath.join(
            self.remote_path,
            pathlib.Path(relative_path).as_posix(),
        )

    def remote_files_exist(self, local_paths: List[pathlib.Path]) -> Set[str]:
        """Return the local paths whose corresponding remote files exist.

        All files are checked in a single SSH invocation. If the SSH call fails,
        retry with exponential backoff.
        """
        if not local_paths:
            return set()

        remote_paths = [self.get_remote_path(path) for path in local_paths]

        # Generate a shell script that outputs the index of every file that exists.
        #
        # Using indexes rather than paths avoids having to parse filenames containing
        # spaces, newlines, etc. on the local side.
        checks = "\n".join(f"test -f {shlex.quote(path)} && printf '%d\\n' {i}" for i, path in enumerate(remote_paths))

        max_attempts = 5
        initial_delay = 1.0

        for attempt in range(max_attempts):
            try:
                result = subprocess.run(
                    [
                        "ssh",
                        f"{self.remote_user}@{self.remote_host}",
                        "bash",
                        "-s",
                    ],
                    input=checks,
                    text=True,
                    capture_output=True,
                    check=True,
                )
                existing = set()
                for line in result.stdout.splitlines():
                    try:
                        existing.add(local_paths[int(line)])
                    except (ValueError, IndexError):
                        # Unexpected output from the remote shell.
                        continue
                return existing
            except subprocess.CalledProcessError:
                if attempt == max_attempts - 1:
                    raise
                delay = initial_delay * (2**attempt)
                time.sleep(delay)
        return set()

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
        while attempt < config.env.max_retries:
            try:
                result = subprocess.run(cmd, capture_output=True, text=True)

                if result.returncode == 0:
                    LOGGER.info(f"✅ Successfully synced {source}")
                    return  # Success, exit function

                # Sync failed
                attempt += 1
                if attempt < config.env.max_retries:
                    # Calculate exponential backoff: 3s, 6s, 12s, 24s...
                    delay = config.env.backoff_factor * (2 ** (attempt - 1))
                    LOGGER.warning(
                        f"⚠️  Sync failed (Attempt {attempt}/{config.env.max_retries}). Retrying in {delay}s..."
                    )
                    LOGGER.warning(f"   Error: {result.stderr.strip()}")
                    time.sleep(delay)
                else:
                    LOGGER.error(f"❌ Failed to sync {source} after {config.env.max_retries} attempts.")
                    LOGGER.error(f"   Final Error: {result.stderr.strip()}")
                    raise RuntimeError(f"Transfer Error: {result.stderr.strip()}") from None
            except Exception as e:
                attempt += 1
                if attempt < config.env.max_retries:
                    delay = config.env.backoff_factor * (2 ** (attempt - 1))
                    LOGGER.warning(
                        f"⚠️  Exception occurred (Attempt {attempt}/{config.env.max_retries}). Retrying in {delay}s..."
                    )
                    LOGGER.warning(f"   Error: {e}")
                    time.sleep(delay)
                else:
                    LOGGER.error(
                        f"❌ Failed to sync {source} after {config.env.max_retries} attempts due to exception."
                    )
                    LOGGER.error(f"   Final Error: {e}")
                    raise RuntimeError(f"Transfer Error [{type(e).__name__}]: {e}") from None
