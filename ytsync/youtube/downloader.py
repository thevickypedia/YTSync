import functools
import logging
import pathlib
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

import yt_dlp
from yt_dlp.utils import DownloadError

from ytsync.modules import checkpoint, config
from ytsync.remote import transfer
from ytsync.youtube import cli, hooks

LOGGER = logging.getLogger("ytsync")


def generate_params(audio_only: bool, destination: pathlib.Path, stats: Dict[str, List[str]]):
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
        "format": "bestaudio/best" if audio_only else "bestvideo+bestaudio/best",
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
    if config.env.cookie_file:
        options["cookiefile"] = str(config.env.cookie_file)
    if config.env.source_address:
        options["source_address"] = str(config.env.source_address)
    if config.env.proxy_url:
        options["proxy"] = str(config.env.proxy_url)

    return options


def download(
    checkpoint_stats: checkpoint.Checkpoint,
    name: str,
    url_file_map: Dict[str, pathlib.Path],
    total_files: int | None,
    destination: pathlib.Path,
    audio_only: bool,
) -> checkpoint.Checkpoint:
    """Downloads the content from a given url and returns download/transfer statistics."""
    start = time.time()
    checkpoint_stats.download_start = config.now()
    stats: Dict[str, List[str]] = {
        "downloaded": [],
        "download_failed": [],
    }

    options = generate_params(audio_only=audio_only, destination=destination, stats=stats)
    transfer_pool = None
    if transfer.rsync.is_enabled:
        transfer_pool = ThreadPoolExecutor(
            max_workers=config.env.max_transfers,
            thread_name_prefix=f"transfer-{name}",
        )
        stats.update(
            {
                "transferred": [],
                "transfer_failed": [],
            }
        )

        def hook(local_path: str) -> None:
            """Function to create a post-process hook to initiate rsync in the background."""
            # noinspection bad-argument-type
            hooks.postprocess_hook(
                local_path=local_path,
                transfer_pool=transfer_pool,
                stats=stats,
            )

        options["post_hooks"] = [hook]

    # noinspection bad-argument-type
    with yt_dlp.YoutubeDL(options) as ydl:
        # yt_dlp is single threaded, but it will fail or skip based on 'ignoreerrors' flag
        # This monotonic loop is to properly capture individual errors and attach custom handlers
        for url, filepath in url_file_map.items():
            if config.env.download_tester:
                LOGGER.info("Download test mode enabled, skipping download for: %s", url)
                filepath.touch(mode=0o644, exist_ok=True)
                stats["downloaded"].append(filepath.name)
                if transfer_pool:
                    hooks.postprocess_hook(
                        local_path=str(filepath),
                        transfer_pool=transfer_pool,
                        stats=stats,
                    )
                continue
            try:
                status = ydl.download([url])
                if audio_only:
                    if status:
                        LOGGER.warning("Download failed for url: %s -> %s", url, filepath.name)
                        stats["download_failed"].append(filepath.name)
                    else:
                        LOGGER.info("Download successful for url: %s -> %s", url, filepath.name)
                        stats["downloaded"].append(filepath.name)
                    continue
            except DownloadError as error:
                LOGGER.warning("Download failed for url: %s -> %s", url, filepath.name)
                cli_attempt = cli.download_track(url, destination, audio_only)
                if cli_attempt and transfer_pool:
                    stats["downloaded"].append(filepath.name)
                    LOGGER.info("CLI attempt was successful, file saved at: %s; initiating rsync...", str(filepath))
                    hooks.postprocess_hook(
                        local_path=str(filepath),
                        transfer_pool=transfer_pool,
                        stats=stats,
                    )
                    continue
                elif cli_attempt:
                    stats["downloaded"].append(filepath.name)
                    LOGGER.info("CLI attempt was successful, file saved at: %s", str(filepath))
                    continue
                else:
                    LOGGER.warning("CLI attempt failed, assuming download failed")
                LOGGER.error(error)
                stats["download_failed"].append(filepath.name)

    if len(stats["download_failed"]) == len(url_file_map):
        if total_files is None:
            raise RuntimeError(f"Failed to download {name!r}")
        else:
            joined = "\n".join(f"• {item}" for item in stats["download_failed"])
            raise RuntimeError(f"{len(url_file_map)} download(s) failed for {name!r}\n{joined}")
    checkpoint_stats.downloaded = stats["downloaded"]
    checkpoint_stats.download_failed = stats["download_failed"]
    if transfer_pool:
        LOGGER.info(
            "Waiting for transfers for %s",
            name,
        )
        transfer_pool.shutdown(wait=True)
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
        playlist_id = transfer.rsync.create_playlist(name) if checkpoint_stats.is_playlist else None
    else:
        try:
            playlist_id = create_local_playlist(destination) if checkpoint_stats.is_playlist else None
        except Exception as error:
            LOGGER.exception("Failed to create local playlist for %s: %s", name, error)
            playlist_id = None
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
