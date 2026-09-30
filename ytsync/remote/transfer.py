import asyncio
import logging
import os
import pathlib
import posixpath
import shlex
import shutil
import subprocess
from typing import List, Set

from ytsync.modules import config, retry
from ytsync.youtube import squire

LOGGER = logging.getLogger("ytsync")


async def runner(cmd: list[str]) -> None:
    """Runs a given command with an asyncio subprocess."""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(),
            timeout=config.env.max_timeout,
        )
    except asyncio.TimeoutError:
        LOGGER.warning("Timeout error occurred while running command: %s", cmd)
        proc.kill()
        await proc.wait()
        raise
    stdout = stdout.decode()
    stderr = stderr.decode()
    if proc.returncode == 0:
        return
    LOGGER.error(
        "Command failed (exit %s): %s\nstdout: %s\nstderr: %s",
        proc.returncode,
        cmd,
        stdout,
        stderr,
    )
    raise subprocess.CalledProcessError(
        returncode=proc.returncode or 1,
        cmd=cmd,
        output=stdout,
        stderr=stderr,
    )


# TODO: Honor profile names, audio and video dir
class Rsync:
    """Rsync object to copy individual files to a remote server.

    >>> Rsync

    """

    def __init__(self):
        """Instantiates the object."""
        self.remote_host = config.env.remote_host
        self.remote_user = config.env.remote_user
        self.remote_path = config.env.remote_path
        self.is_enabled = all((self.remote_host, self.remote_user, self.remote_path, shutil.which("rsync") is not None))

    def get_remote_path(self, local_path: pathlib.Path) -> str:
        """Use the existing local filepath to derive the filepath in the remote server.

        Args:
            local_path: Local filepath.

        Returns:
            str:
            Filepath in the remote server.
        """
        # 'local_path' is the filepath, which is within 'audio' or 'video' directory; hence the '.parent.parent'
        root_path = local_path.parent.parent.resolve()
        relative_path = os.path.relpath(local_path, str(root_path))
        return posixpath.join(
            self.remote_path,
            pathlib.Path(relative_path).as_posix(),
        )

    async def exist_check(self, checks: str, local_paths: List[pathlib.Path]) -> Set[str]:
        """Checks if a list of files exists on the remote server and returns the existing ones."""
        proc = await asyncio.create_subprocess_shell(
            f"ssh {self.remote_user}@{self.remote_host} bash -s",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(input=checks.encode()),
                timeout=config.env.max_timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return set()
        if proc.returncode == 0:
            existing = set()
            for line in stdout.decode().splitlines():
                try:
                    existing.add(local_paths[int(line)])
                except (ValueError, IndexError):
                    # Unexpected output from the remote shell.
                    continue
            return existing
        return set()

    async def remote_files_exist(self, local_paths: List[pathlib.Path]) -> Set[str]:
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
        checks += "\nexit 0\n"

        existing = await retry.retry(
            name=self.exist_check.__name__,
            function=lambda: self.exist_check(checks=checks, local_paths=local_paths),
            max_retries=2,
            backoff_factor=1,
        )
        return existing.response or set()

    async def run(self, source: pathlib.Path) -> None:
        """Syncs a file to a remote server with exponential backoff retry logic."""
        destination = self.get_remote_path(source)
        remote_location = f"{self.remote_user}@{self.remote_host}:{destination}"
        LOGGER.info("Syncing [%s]: '%s' -> '%s'", squire.size_converter(source.stat().st_size), source, remote_location)

        remote_parent = posixpath.dirname(destination)
        LOGGER.debug("remote_path=%r", self.remote_path)
        LOGGER.debug("destination=%r", destination)
        LOGGER.debug("remote_parent=%r", remote_parent)
        mkdir_cmd = [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            f"{self.remote_user}@{self.remote_host}",
            f"mkdir -p {shlex.quote(remote_parent)}",
        ]
        LOGGER.info("Creating remote directory: %s", remote_parent)
        await retry.retry(
            name=runner.__name__,
            function=lambda: runner(cmd=mkdir_cmd),
            raise_error=True,
        )

        cmd = [
            "rsync",
            "-avzi",
            "--partial",
            "-e",
            "ssh -o StrictHostKeyChecking=no",
            str(source),
            remote_location,
        ]
        LOGGER.info("Starting rsync for file: %s", source)
        await retry.retry(
            name=runner.__name__,
            function=lambda: runner(cmd=cmd),
            raise_error=True,
        )

    async def create_playlist(self, name: str, extension: str) -> str:
        """Create a .m3u file on the remote machine."""
        remote_loc = posixpath.join(self.remote_path, name)
        filepath = posixpath.join(self.remote_path, name, f"{name}.m3u")
        LOGGER.debug("Remote location: %s", remote_loc)
        LOGGER.info("Playlist file: %s", filepath)
        cmd = [
            "ssh",
            f"{self.remote_user}@{self.remote_host}",
            f"mkdir -p {shlex.quote(remote_loc)} && "
            f"cd {shlex.quote(remote_loc)} && "
            f"ls *{extension} > {shlex.quote(filepath)}",
        ]
        LOGGER.debug("Command: %s", cmd)
        proc = await asyncio.create_subprocess_shell(
            shlex.join(cmd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            _stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=config.env.max_timeout,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to create playlist for {name}: {stderr.decode()}")
        return filepath


rsync = Rsync()
