# PodWave

PodWave is deployed on `home-core` as an isolated Compose project and served at
`https://podwave.home.arpa/`. Caddy terminates TLS with the existing private
HomeCompute CA and requires HTTP Basic Auth before forwarding traffic. The site
is available only through the existing private LAN/Tailscale Caddy listeners;
there is no public port or Tailscale Funnel.

The app image is built from the PodWave source pinned to commit
`fcd23dcbebf6bce5d48f68b3b79ce7e3ea469fad`. Its uv/Python base is pinned by
digest in the tracked Dockerfile. The service has no host port, no Docker socket,
no capabilities, a read-only root filesystem, and a dedicated bridge shared
only with Caddy. Its separate default bridge provides outbound provider access.

The app's server configuration and generated episode files live below
`/srv/state/podwave`; NixOS provisions the directory for the container's
non-root UID. This path is covered by the host's `/srv/state` application backup.
The Caddy login hash and recovery password live in the separately protected
`/srv/state/podwave-auth` directory and are also covered by that backup. On the
first deployment, the release creates a random login. Retrieve it on home-core
with `sudo cat /srv/state/podwave-auth/password.txt`; the username is `podwave`.
Keep the password in the household password manager. Do not copy it into Git or
the app's provider settings.

The authoritative local DNS input is [`config/podwave-dns.json`](../../config/podwave-dns.json).
Install its `podwave.home.arpa` A record (`192.168.30.122`, TTL 300) in the
home LAN resolver. Clients on the tailnet must use the existing `home.arpa`
split DNS resolver. Trust the HomeCompute CA on clients as documented in the
[Homepage runbook](../homepage/README.md).

After opening the URL and signing in, configure server-side LLM and TTS
providers in PodWave's Settings page. Their API keys are stored in the app's
`/srv/state/podwave/config.json` with mode `0600`; the app masks keys in API
responses. Browser-only provider settings remain in that browser's local
storage. Do not use real provider keys in shell arguments or commit them.

Normal updates go through the reviewed HomeCompute release on `origin/master`.
The guarded `scripts/deploy-home-core.sh` lifecycle builds the image, applies
the control-plane network and authenticated Caddy route, then starts PodWave
and waits for its health check. The same project file is used to inspect or
stop the workload:

```bash
sudo docker compose -f /srv/homecompute/current/deploy/podwave/compose.yaml ps
sudo docker compose -f /srv/homecompute/current/deploy/podwave/compose.yaml logs --tail 100
sudo docker compose -f /srv/homecompute/current/deploy/podwave/compose.yaml down
```

Before restoring `/srv/state/podwave`, stop the project, restore ownership to
UID/GID 1000, then redeploy the reviewed HomeCompute release. Restore
`/srv/state/podwave-auth` with mode `0700` and its files with mode `0600` to keep
the login stable. To rotate a lost or exposed password, run
`sudo python3 /srv/homecompute/current/scripts/setup-podwave-auth.py --rotate`
on home-core, read the new password from the path above, then recreate Caddy:

```bash
sudo docker compose --env-file /etc/homecompute/control-plane.env \
  -f /srv/homecompute/current/deploy/control-plane/compose.yaml \
  up -d --force-recreate caddy
```
