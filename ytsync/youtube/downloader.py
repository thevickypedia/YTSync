import asyncio
import functools
import logging
import pathlib
import time
from typing import Any, Awaitable, Dict, List, Tuple

import yt_dlp
from yt_dlp.utils import DownloadError

from ytsync.modules import checkpoint, config
from ytsync.remote import transfer
from ytsync.youtube import cli, hooks, squire

LOGGER = logging.getLogger("ytsync")


def generate_params(audio_only: bool, destination: pathlib.Path, stats: Dict[str, List[str]]) -> Dict[str, Any]:
    """Generate the parameters for the yt_dlp module.

    Args:
        audio_only: Bool flag to indicate if only audio should be downloaded.
        destination: Destination path.
        stats: Statistics dictionary.

    Returns:
        Dict[str, Any]:
        Returns the parameters for the yt_dlp module.
    """
    options: Dict[str, Any] = {
        "logger": LOGGER,
        "quiet": True,
        "format": "bestaudio/best" if audio_only else "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]",
        "ignoreerrors": False,
        "outtmpl": str(destination.joinpath(config.YT_FILENAME_TEMPLATE)),
        "writethumbnail": True,
    }
    if audio_only:
        options["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "0",
            },
            {
                "key": "FFmpegMetadata",
            },
            {
                "key": "EmbedThumbnail",
            },
        ]
    else:
        options["postprocessors"] = [
            {
                "key": "FFmpegVideoRemuxer",
                "preferedformat": "mp4",
            },
            {
                "key": "FFmpegMetadata",
            },
            {
                "key": "EmbedThumbnail",
            },
        ]
        options["progress_hooks"] = [
            functools.partial(
                hooks.download_progress_hook,
                stats=stats,
            )
        ]

    return squire.add_optional_params(options)


async def download_ydl(
    ydl: yt_dlp.YoutubeDL, url: str, filepath: pathlib.Path, stats: Dict[str, List[str]], audio_only: bool
) -> None:
    """Downloads using the yt-dlp library."""
    with ydl:
        if ydl.download([url]):
            raise DownloadError(msg=f"Download failed for url: {url} -> {filepath.name}")
        if audio_only:
            LOGGER.info("Download successful for url: %s -> %s", url, filepath.name)
            stats["downloaded"].append(filepath.name)


async def download_cli(
    url: str,
    filepath: pathlib.Path,
    stats: Dict[str, List[str]],
    audio_only: bool,
    destination: pathlib.Path,
) -> Tuple[bool, Awaitable | None]:
    """Downloads using the yt-dlp CLI."""
    LOGGER.warning("Download failed for url: %s -> %s", url, filepath.name)
    cli_attempt = await cli.download_track(url, destination, audio_only)
    if cli_attempt and transfer.rsync.is_enabled:
        stats["downloaded"].append(filepath.name)
        LOGGER.info("CLI attempt was successful, file saved at: %s; initiating rsync...", str(filepath))
        task = hooks.postprocess_hook(
            local_path=str(filepath),
            stats=stats,
        )
        return cli_attempt, task
    elif cli_attempt:
        stats["downloaded"].append(filepath.name)
        LOGGER.info("CLI attempt was successful, file saved at: %s", str(filepath))
    return cli_attempt, None


async def download_alt(
    ydl: yt_dlp.YoutubeDL,
    url: str,
    filepath: pathlib.Path,
    stats: Dict[str, List[str]],
    audio_only: bool,
    destination: pathlib.Path,
) -> Awaitable | None:
    """Downloader function for alternative download methods.

    Args:
        ydl: YoutubeDL object.
        url: URL to submit the request.
        filepath: Path to the file to download.
        stats: Dictionary to store/append statistics.
        audio_only: Whether to download only the audio.
        destination: Path to save the downloaded file.

    Returns:
        Awaitable | None:
        Returns an awaitable task if there is an async task to gather.
    """
    task = None
    # MARK: If the file already exists [OR] download tester is enabled, skip the download and initiate rsync if enabled
    # A filepath can exist and be present in the url_file_map, if the file is available locally, but missing in remote
    # Partially downloaded files WILL NEVER have the same file extension as the final file, so '> 0' check is sufficient
    if config.env.download_tester or (filepath.exists() and filepath.stat().st_size > 0):
        if config.env.download_tester:
            LOGGER.info("Download tester enabled, skipping [%s] - %s", url, filepath)
            filepath.touch(mode=0o644, exist_ok=True)
        else:
            LOGGER.info(
                "File already exists, skipping [%s] - %s [%s]",
                url,
                filepath,
                squire.size_converter(filepath.stat().st_size),
            )
        stats["downloaded"].append(filepath.name)
        if transfer.rsync.is_enabled:
            return hooks.postprocess_hook(
                local_path=str(filepath),
                stats=stats,
            )
        return None
    try:
        await download_ydl(ydl, url, filepath, stats, audio_only)
    except DownloadError as error:
        # 'task' exists only when 'cli_result' is a non-zero value
        cli_result, task = await download_cli(url, filepath, stats, audio_only, destination)
        if not cli_result:
            LOGGER.warning("CLI attempt failed, assuming download failed")
            LOGGER.error(error)
            stats["download_failed"].append(filepath.name)
    return task


async def download(
    checkpoint_stats: checkpoint.Checkpoint,
    preprocess_stats: squire.PreProcessor,
) -> checkpoint.Checkpoint:
    """Downloads the content from a given url and returns download/transfer statistics.

    Args:
        checkpoint_stats: Checkpoint object.
        preprocess_stats: PreProcessor object.

    Returns:
        checkpoint.Checkpoint:
        Returns the updated Checkpoint object.
    """
    start = time.time()
    checkpoint_stats.download_start = config.now()
    stats: Dict[str, List[str]] = {
        "downloaded": [],
        "download_failed": [],
    }

    name = checkpoint_stats.name
    url_file_map = preprocess_stats.url_file_map
    total_files = preprocess_stats.total_files or len(preprocess_stats.url_file_map)
    destination = checkpoint_stats.initial_destination
    audio_only = checkpoint_stats.source_system.audio_only
    options = generate_params(audio_only=audio_only, destination=destination, stats=stats)
    transfer_pool = []
    if transfer.rsync.is_enabled:
        stats.update(
            {
                "transferred": [],
                "transfer_failed": [],
            }
        )

        def hook(local_path: str) -> None:
            """Function to create a post-process hook to initiate rsync in the background."""
            # noinspection bad-argument-type
            if pending_task := hooks.postprocess_hook(
                local_path=local_path,
                stats=stats,
            ):
                transfer_pool.append(pending_task)

        options["post_hooks"] = [hook]

    # noinspection bad-argument-type
    with yt_dlp.YoutubeDL(options) as ydl:
        # yt_dlp is single threaded, but it will fail or skip based on 'ignoreerrors' flag
        # This monotonic loop is to properly capture individual errors and attach custom handlers
        for url, filepath in url_file_map.items():
            if task := await download_alt(ydl, url, filepath, stats, audio_only, destination):
                transfer_pool.append(task)

    if len(stats["download_failed"]) == len(url_file_map):
        if total_files is None:
            raise RuntimeError(f"Failed to download {name!r}")
        else:
            joined = "\n".join(f"• {item}" for item in stats["download_failed"])
            raise RuntimeError(f"{len(url_file_map)} download(s) failed for {name!r}\n{joined}")
    checkpoint_stats.downloaded = stats["downloaded"]
    checkpoint_stats.download_failed = stats["download_failed"]
    if transfer.rsync.is_enabled:
        LOGGER.info("Waiting for transfers for %s", name)
        await asyncio.gather(*transfer_pool)
        checkpoint_stats.transferred = stats["transferred"]
        checkpoint_stats.transfer_failed = stats["transfer_failed"]
        transferred = len(stats["transferred"])
        transfer_failed = len(stats["transfer_failed"])
        if not any((transferred, transfer_failed)):
            raise RuntimeError(f"No files transferred for {name!r}")
        if not transferred and transfer_failed:
            joined = "\n".join(f"• {item}" for item in stats["transfer_failed"])
            raise RuntimeError(f"All transfers failed for {name!r}\n{joined}")
        LOGGER.info("All transfers completed for %s " "(successful=%d, failed=%d)", name, transferred, transfer_failed)
        playlist_id = await transfer.rsync.create_playlist(name) if checkpoint_stats.is_playlist else None
    else:
        try:
            playlist_id = create_local_playlist(destination) if checkpoint_stats.is_playlist else None
        except Exception as error:
            LOGGER.exception("Failed to create local playlist for %s: %s", name, error)
            playlist_id = "Failed to create playlist for {!r}: {}".format(name, error)
    checkpoint_stats.playlist_id = playlist_id
    checkpoint_stats.runtime = time.time() - start
    return checkpoint_stats


def create_local_playlist(destination: pathlib.Path) -> str | None:
    """Create a .m3u file on the local machine."""
    destination.mkdir(parents=True, exist_ok=True)
    filepath = destination / f"{destination.name}.m3u"
    if files := [file.name for file in destination.glob("*.mp3")]:
        with filepath.open("w", encoding="utf-8") as playlist_file:
            playlist_file.write("\n".join(files) + "\n")
        return str(filepath)
    LOGGER.warning("No eligible files found in %s", destination)
    return None
