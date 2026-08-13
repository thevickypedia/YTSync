**Deployments**

![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-blue)
[![Pypi Publish](https://github.com/thevickypedia/YTSync/actions/workflows/python-publish.yml/badge.svg)](https://github.com/thevickypedia/YTSync/actions/workflows/python-publish.yml)
[![Docker Publish](https://github.com/thevickypedia/YTSync/actions/workflows/docker.yml/badge.svg)](https://github.com/thevickypedia/YTSync/actions/workflows/docker.yml)

# YTSync
YTSync is a lightweight API, equipped with Telegram Bot to download a playlist and asynchronously transfer it to a remote server via rsync (ssh)

## Features
- Supports both webhooks and long-polling
- Built-in exponential back off for remote transfers
- Sequential downloads (to prevent requests being throttled/blocked)
- Optional flag to delete local files after transferring to remote server
- Built-in support to check file presence on remote server before requesting download/file-transfer

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

## License & copyright

&copy; Vignesh Rao

Licensed under the [MIT License][license]

[license]: https://github.com/thevickypedia/YTSync/blob/main/LICENSE
