**Deployments**

![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)

[![Pypi Publish](https://github.com/thevickypedia/YTSync/actions/workflows/python-publish.yml/badge.svg)](https://github.com/thevickypedia/YTSync/actions/workflows/python-publish.yml)

[![Docker Publish](https://github.com/thevickypedia/YTSync/actions/workflows/docker.yml/badge.svg)](https://github.com/thevickypedia/YTSync/actions/workflows/docker.yml)

[![PyPI version shields.io](https://img.shields.io/pypi/v/YTSync)][pypi]
[![Pypi-format](https://img.shields.io/pypi/format/YTSync)](https://pypi.org/project/YTSync/#files)
[![Pypi-status](https://img.shields.io/pypi/status/YTSync)][pypi]

# YTSync
YTSync is a lightweight API, equipped with Telegram Bot to download a playlist and asynchronously transfer it to a remote server via rsync (ssh)

## Features

* Supports both webhooks and long-polling
* Users can switch between webhooks and long-polling through API
* Sequential downloads (to prevent requests being throttled/blocked)
* Cold start (delayed) for the first download with a custom cooldown interval
* Built-in retry mechanism with an exponential back off factor for remote transfers
* Automatically deletes [Optional] local files after transferring to a remote server
* Built-in support to check file presence on the remote server before requesting download/file-transfer

### Environment variables

###### Server Settings
* **host**: Hostname to run API server. _Defaults to `0.0.0.0` [OR] `localhost`_
* **port**: Port number to run the API server. _Defaults to `4483`_
* **tz**: IANA time zone identifier. _Defaults to server's local timezone_
* **log_config**: Dict config or filepath for log configuration. _Defaults to `logging.basicConfig`_

###### Telegram Settings
* **bot_token**: Telegram bot token. _Required for telegram access_
* **bot_chat_ids**: List of bot ids to allow. _Required for telegram access_
* **bot_users**: List of bot usernames to allow. _Required for telegram access_
* **poll_interval**: Number of seconds between each request to poll. Defaults to `2`
* **bot_webhook**: Telegram bot webhook URL. [Optional]
* **bot_webhook_ip**: Webhook IP address. [Optional]
* **bot_endpoint**: API endpoint to serve the webhook. _Defaults to `/telegram-webhook`_
* **bot_secret**: Secret key to verify webhook requests. [Optional]
* **bot_certificate**: Certificate filepath for webhook server (in case of self-signed certificate) [Optional]

###### API Settings
* **apikey**: Key to access via API. _Required for API access; defaults to `bot_token`_

###### yt-dlp Settings [Optional]
* **cookie_file**: Path to the cookie file.
* **source_address**: IP address for the requesting source.
* **proxy_url**: URL of the proxy server.

###### FileIO Settings
* **data_dir**: Directory to store the database. _Defaults to `data`_
* **logs_dir**: Directory to store logs. _Defaults to `logs`_
* **download_dir**: Directory to store downloaded files. _Defaults to `downloads`_

###### Concurrency & Tolerance Settings
* **max_transfers**: Maximum number of concurrent transfers to perform. _Defaults to the number of CPU cores_
* **max_retries**: Maximum number of retries for rsync and telegram polling. _Defaults to `10`_
* **backoff_factor**: Back off factor between each retry attempt. _Defaults to `3`_
* **max_error_threshold**: Percentage of individual URLs to verify before downloading the entire playlist. _Defaults to `30`_
* **response_timeout**: Maximum number of seconds to wait before timing out the client request. _Defaults to `30`_

###### Queue Settings
* **delayed_start**: Perform a cold start (delayed) for the first download. _Defaults to `False`_
* **next_buffer**: Number of seconds to simulate time taken for a download. _Defaults to `60`_
* **cooldown_interval**: Number of seconds to wait before processing next in queue. _Defaults to `300`_

###### Remote Settings [Optional]
* **remote_host**: Hostname [OR] IP address of the remote server.
* **remote_user**: Username to connect to the remote server.
* **remote_path**: Directory path on the remote server, to transfer downloaded files to.
* **delete_after_sync**: Boolean flag to delete local files after transferring to the remote server.

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
