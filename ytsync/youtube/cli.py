import logging
import pathlib
import shutil
import subprocess

from ytsync.modules import config

LOGGER = logging.getLogger("ytsync")


def get_cli_command(
    url: str,
    root_cmd: str,
    destination: pathlib.Path,
    audio_only: bool,
) -> str:
    """Get the CLI command to download a YT url.

    Args:
        url: YT URL to download.
        root_cmd: Root command path for yt-dlp.
        destination: Path to save the downloaded file.
        audio_only: Bool flag to indicate if only audio should be downloaded.

    Returns:
        str:
        Returns the full CLI command to download the video.
    """
    if audio_only:
        args = (
            "-f bestaudio "
            "-x "
            "--audio-format mp3 "
            "--audio-quality 0 "
            "--embed-thumbnail "
            "--embed-metadata "
            "-o"
        )
    else:
        args = "-f bestvideo+bestaudio/best " "--embed-thumbnail " "--embed-metadata " "-o"
    return f'{root_cmd} {args} "{str(destination.joinpath(config.YT_FILENAME_TEMPLATE))}" "{url}"'


def download_track(url: str, destination: pathlib.Path, audio_only: bool) -> bool:
    """Download a track using the yt-dlp CLI command.

    Args:
        url: URL to download.
        destination: Path to save the downloaded file.
        audio_only: Bool flag to indicate if only audio should be downloaded.

    Returns:
        bool:
        Returns a boolean flag to indicate if the download was successful.
    """
    if yt_dlp := shutil.which("yt-dlp"):
        cmd = get_cli_command(url=url, root_cmd=yt_dlp, destination=destination, audio_only=audio_only)
        LOGGER.debug("Running the command: %s", cmd)
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, shell=True)
        except (subprocess.SubprocessError, subprocess.CalledProcessError) as error:
            if isinstance(error, subprocess.CalledProcessError):
                result = error.output.decode(encoding="UTF-8").strip()
                LOGGER.warning("[%d]: %s", error.returncode, result)
            else:
                LOGGER.warning(error)
            return False
        for output in result.stdout.splitlines():
            LOGGER.debug(output)
        for output in result.stderr.splitlines():
            LOGGER.warning(output)
        return result.returncode == 0
    LOGGER.warning("yt-dlp not found in PATH for a CLI download attempt.")
    return False
