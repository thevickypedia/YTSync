**Deployments**

![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)

[![Pypi Publish](https://github.com/thevickypedia/YTSync/actions/workflows/python-publish.yml/badge.svg)](https://github.com/thevickypedia/YTSync/actions/workflows/python-publish.yml)

[![Docker Publish](https://github.com/thevickypedia/YTSync/actions/workflows/docker.yml/badge.svg)](https://github.com/thevickypedia/YTSync/actions/workflows/docker.yml)

[![PyPI version shields.io](https://img.shields.io/pypi/v/YTSync)][pypi]
[![Pypi-format](https://img.shields.io/pypi/format/YTSync)](https://pypi.org/project/YTSync/#files)
[![Pypi-status](https://img.shields.io/pypi/status/YTSync)][pypi]

# YTSync
YTSync is a lightweight API, equipped with a Telegram Bot, to download audio/video from a URL or playlist and asynchronously transfer it to a remote server via rsync (ssh).

## Features

* Supports both webhooks and long-polling for Telegram
* Users can switch between webhooks and long-polling through the API
* Multi-profile support — isolate API keys, Telegram bot credentials, and storage per profile
* Recurring, schedule-based playlist tracking (hourly/daily/weekly/monthly) via Telegram or API
* Sequential downloads (to prevent requests being throttled/blocked)
* Cold start (delayed) for the first download with a custom cooldown interval
* Built-in retry mechanism with an exponential backoff factor for remote transfers
* Automatically deletes [Optional] local files after transferring to a remote server
* Built-in support to check file presence on the remote server before requesting download/file-transfer
* Queryable download queue and historical checkpoints via API, with configurable checkpoint retention

### Environment variables
> YTSync can be configured using environment variables, which can be set in a `.env` (`SECRETS_PATH`) or `.env.json` (`CONFIG_PATH`) file.

###### Server Settings
* **host**: Hostname to run API server. _Defaults to `0.0.0.0` [OR] `localhost`_
* **port**: Port number to run the API server. _Defaults to `4483`_
* **tz**: IANA time zone identifier. _Defaults to server's local timezone_
* **log_config**: Dict config or filepath for log configuration. _Defaults to `logging.basicConfig`_

###### Profile Settings
* **name**: Name of the profile.
* **apikey**: Key to access via API. _Required for API access_
* **bot_chat_id**: Telegram chat ID allowed to use this profile's bot. _Required for telegram access_
* **bot_username**: Telegram username allowed to use this profile's bot. _Required for telegram access_
> **Note**: Each profile maps to exactly one Telegram chat ID/username pair. To allow multiple Telegram users, define multiple profiles.

###### Telegram Settings
* **bot_token**: Telegram bot token. _Required for telegram access_
* **poll_interval**: Number of seconds between each request to poll. Defaults to `2`
* **bot_webhook**: Telegram bot webhook URL. [Optional]
* **bot_webhook_ip**: Webhook IP address. [Optional]
* **bot_endpoint**: API endpoint to serve the webhook. _Defaults to `/telegram-webhook`_
* **bot_secret**: Secret key to verify webhook requests. [Optional]
* **bot_certificate**: Certificate filepath for webhook server (in case of self-signed certificate) [Optional]

###### yt-dlp Settings [Optional]
* **download_tester**: Boolean flag to skip download, and create placeholder files for testing. _Defaults to `False`_
* **cookie_file**: Path to the cookie file.
* **source_address**: IP address for the requesting source.
* **proxy_url**: URL of the proxy server.

###### FileIO Settings
* **data_dir**: Directory to store the database. _Defaults to `data`_
* **logs_dir**: Directory to store logs. _Defaults to `logs`_
* **audio_dir**: Directory to store audio files. _Defaults to `audio`_
* **video_dir**: Directory to store video files. _Defaults to `video`_
> **Note**: `audio_dir` and `video_dir` are the base directories, under which each profile gets its own subdirectory<br>
> For example, if the profile name is `profile1`, audio files are stored in `audio/profile1` and video files are stored in `video/profile1`

###### Concurrency & Tolerance Settings
* **max_retries**: Maximum number of retries for rsync and telegram polling. _Defaults to `10`_
* **max_timeout**: Maximum number of seconds to wait before timing out CLI downloads, rsync transfers, and other remote SSH operations. _Defaults to `60`_
* **backoff_factor**: Back off factor between each retry attempt. _Defaults to `3`_
* **max_error_threshold**: Percentage of individual URLs to verify before downloading the entire playlist. _Defaults to `30`_
* **response_timeout**: Maximum number of seconds to wait before timing out the client request. _Defaults to `30`_

###### Queue Settings
* **delayed_start**: Perform a cold start (delayed) for the first download. _Defaults to `False`_
* **next_buffer**: Number of seconds to simulate time taken for a download. _Defaults to `60`_
* **cooldown_interval**: Number of seconds to wait before processing next in queue. _Defaults to `300`_

###### Analytics Settings [Optional]
* **checkpoint_retention**: Number of days/weeks/months to retain the checkpoint data (up to `90` days). _Defaults to `3d`_

###### Remote Settings [Optional]
* **remote_host**: Hostname [OR] IP address of the remote server.
* **remote_user**: Username to connect to the remote server.
* **remote_path**: Directory path on the remote server, to transfer downloaded files to.
* **delete_after_sync**: Boolean flag to delete local files after transferring to the remote server.
> **Note**: `remote_path` is the base directory, under which the local structure is mirrored by profile and media type<br>
> For example, if the profile name is `profile1`, audio files are transferred to `remote_user@remote_host:remote_path/audio/profile1` and video files are transferred to `remote_user@remote_host:remote_path/video/profile1`

#### Limitations
* Environment variables can be read from a `.env` or `.json` file, but docker context only supports `.env` files.
* Profiles are created by default within the chosen `audio_dir` and `video_dir` directories.
* YTSync does not support concurrent downloads, and will download sequentially to avoid being throttled/blocked by the source server.
* YTSync runs on a single process with a single event loop. Blocking operations (metadata lookups, downloads, database I/O) are offloaded to a thread pool via `asyncio.to_thread` so the server stays responsive, but downloads themselves are still processed one at a time, in order; transfers are submitted as soon as each file finishes downloading.

### SSH setup

> Allow remote server connection without requiring a password [OR] private key during run-time
```shell
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
ssh-copy-id user@receiver_ip
ssh user@receiver_ip
```

### Docker

> Mount the local `~/.ssh` volume to allow reading known hosts
```shell
docker run \
  -v ~/.ssh/id_ed25519:/root/.ssh/id_ed25519:ro \
  -v ~/.ssh/known_hosts:/root/.ssh/known_hosts:ro \
  ytsync
```

## Linting
`pre-commit` will ensure linting

**Requirement**
```shell
pip install pre-commit
```

**Usage**
```shell
pre-commit run --all-files
```

## Pypi Package
[![pypi-module][pypi-repo-logo]][pypi-repo]

[https://pypi.org/project/YTSync/][pypi]

## License & copyright

&copy; Vignesh Rao

Licensed under the [MIT License][license]

[license]: https://github.com/thevickypedia/YTSync/blob/main/LICENSE
[pypi]: https://pypi.org/project/YTSync/
[pypi-repo]: https://packaging.python.org/tutorials/packaging-projects/
[pypi-repo-logo]: https://img.shields.io/badge/Software%20Repository-pypi-1f425f.svg
