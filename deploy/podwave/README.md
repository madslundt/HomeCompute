# PodWave

PodWave is deployed on `home-core` as an isolated Compose project and served at
`https://podwave.home.arpa/`. Caddy terminates TLS with the existing private
HomeCompute CA and requires HTTP Basic Auth before forwarding traffic. The site
is available only through the existing private LAN/Tailscale Caddy listeners;
there is no public port or Tailscale Funnel.

The app image is built from the PodWave source pinned to commit
`4ba246c5e1d4b55959bbc3a14648bf688be12dcb` (version 1.7.0). Its uv/Python base
is pinned by digest in the tracked Dockerfile. The service has no host port, no
Docker socket, no capabilities, a read-only root filesystem, and a dedicated
bridge shared only with Caddy. Caddy publishes the `ai.home.arpa` alias only on
that bridge, so server-side provider traffic reaches the internal AI route
without using the host's LAN or Tailscale listener. PodWave trusts the mounted
HomeCompute root CA alongside the image's public roots. Its separate default
bridge provides outbound provider access.

For the built-in HomeCompute gateway, use `https://ai.home.arpa:8443/v1` and
model `automation-moe`. The URL resolves to Caddy only inside the PodWave bridge;
the gateway adds its restricted network inference credential. Do not put the
LiteLLM master or network inference key in PodWave settings.

For HomeCompute TTS, select the OpenAI-compatible provider on the same base URL,
model `omnivoice`, voice A `danish-default`, and voice B `english-default`. The
gateway key is scoped for these aliases. The release migrates only the old
`OmniVoice` model and placeholder household voice to these supported values;
other user-selected provider settings are preserved. The LLM connection probe
allows 128 output tokens so local reasoning models can finish their reasoning
before returning the short test response.

PodWave 1.6 supports multiple named LLM and TTS profiles. Its built-in migration
preserves the existing single LLM and TTS profiles, including their model URLs,
models, voices, and stored credentials.

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
and waits for its health check. If an unrelated full-stack prerequisite blocks
that rollout, the PodWave-only lifecycle can update this already-provisioned
workload without changing the full host release marker:

```bash
./scripts/homecompute deploy-podwave --revision FULL_HOME_COMPUTE_COMMIT_SHA
```

It requires the existing Caddy route, PodWave network, CA, and state directory.
Before replacing the container it stops PodWave briefly and makes a
root-only, checksummed archive of `/srv/state/podwave` at
`/srv/state/control-plane/backups/podwave/`. The full app state is about 15 MB
on the current host. It checks the authenticated HTTPS URL, then records the
deployed source at
`/srv/homecompute/podwave-current` and its HomeCompute revision in
`/var/lib/homecompute/podwave-deployed-revision`. A full host deployment updates
these markers after all workloads succeed. `podwave-previous` retains the
previous application release for inspection. The pre-upgrade archive is local
to home-core and contains the app configuration and generated files; keep any
separate off-host backup policy in place.

To roll back a PodWave config migration, verify the archive, restore it while
PodWave is stopped, then deploy the previous full HomeCompute revision. The
workstation CLI uses the latest published PodWave deploy helper when that older
revision predates the isolated lifecycle:

```bash
ssh home-core 'sudo sha256sum --check /srv/state/control-plane/backups/podwave/ARCHIVE.tar.gz.sha256'
ssh home-core 'sudo docker compose -f /srv/homecompute/podwave-current/deploy/podwave/compose.yaml stop podwave'
ssh home-core 'sudo tar --numeric-owner --xattrs --acls --extract --gzip --file /srv/state/control-plane/backups/podwave/ARCHIVE.tar.gz -C /srv/state'
ssh home-core 'basename "$(readlink -f /srv/homecompute/podwave-previous)"'
./scripts/homecompute deploy-podwave --revision PREVIOUS_HOME_COMPUTE_COMMIT_SHA
```

Use the PodWave release pointer to inspect or stop the workload:

```bash
sudo docker compose -f /srv/homecompute/podwave-current/deploy/podwave/compose.yaml ps
sudo docker compose -f /srv/homecompute/podwave-current/deploy/podwave/compose.yaml logs --tail 100
sudo docker compose -f /srv/homecompute/podwave-current/deploy/podwave/compose.yaml down
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
