# YTSync
YouTube Sync

### Docker

> Allow remote server connection without requiring password [OR] private key
```shell
ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519
ssh-copy-id user@receiver_ip
ssh user@receiver_ip
```

> Mount the local `~/.ssh` volume to allow reading known hosts
```shell
docker run \
  -v ~/.ssh/id_ed25519:/root/.ssh/id_ed25519:ro \
  -v ~/.ssh/known_hosts:/root/.ssh/known_hosts:ro \
  ytsync
```
