# T3 Code on Home Core

T3 Code is a private coding workspace for personal projects. The application
runs in its own Compose project on `home-core`. The upstream Linux x64 T3 Code
and Codex CLI artifacts are verified against the SHA-256 pins in the Dockerfile.
The container runs as UID/GID 12073 with a read-only root filesystem, resource
limits, no capabilities, and a single persistent bind mount at `/data`. It
receives no host checkout, SSH keys, Docker socket, or production credentials.

The managed policy disables T3 Connect, webhooks, browser-cookie import, cloud
preview tunnels, product telemetry, and in-app updates. Codex is the installed
provider and uses approval-required mode by default. The container can reach
the network for provider login, model requests, and Git. Use only projects
approved for the provider you sign in to.

## Connect from your workstation

Open **T3 Code** in Homepage or visit [t3code.home.arpa](https://t3code.home.arpa/)
from an approved home network. Use the home DNS resolver and trust the
[HomeCompute Caddy CA](../homepage/README.md). Caddy reaches the application
over a dedicated, internal Docker network. T3 Code requires an application
session.

For a new browser or device, create a five-minute, single-use link:

```sh
ssh home-core 'sudo docker exec homecompute-t3code-t3code-1 t3 auth pairing create --ttl 5m --base-url https://t3code.home.arpa'
```

Open the printed link, then use the Homepage card for future visits. Generate
a separate link for each client. Manage established sessions under **Settings
→ Sharing & access**.

To start Codex threads, sign in with a ChatGPT account authorized for the
projects you plan to use. The login command runs inside the container and
stores its credentials in the service's private state:

```sh
ssh -t home-core 'sudo docker exec -it homecompute-t3code-t3code-1 codex login --device-auth'
```

Complete the browser prompt shown by Codex. In T3 Code, open or clone a Git
repository under `/data/projects` and select Codex when creating a thread.
Access to private Git repositories requires separately scoped Git credentials.

## Deploy and inspect

Use this dedicated lifecycle on `home-core` from a reviewed commit published
on `origin/master`:

```sh
revision=FULL_RELEASE_SHA
release=/srv/homecompute/releases/$revision
sudo git clone https://github.com/madslundt/HomeCompute.git "$release"
sudo git -C "$release" checkout --detach "$revision"
sudo python3 "$release/scripts/setup-t3code-access.py"
sudo bash "$release/scripts/deploy-t3code.sh" "$revision"
```

For a release directory already present, verify its checkout and run just the
installer. It uses the shared deployment lock, validates state ownership,
builds the pinned image, tests the Codex sandbox in a disposable container,
and waits for container health. Inspect the running image, Compose source, and
management policy:

```sh
sudo docker inspect --format '{{.Config.Image}} {{.State.Health.Status}} {{index .Config.Labels "com.docker.compose.project.config_files"}}' homecompute-t3code-t3code-1
sudo docker exec homecompute-t3code-t3code-1 t3 policy show
sudo docker exec homecompute-t3code-t3code-1 codex login status
```

The access installer adds the virtual host and the internal Docker network to
the healthy gateway. The normal Home Core release also declares both. Add the
record in [t3code-dns.json](../../config/t3code-dns.json) in UniFi Network under
**Settings → Policy Engine → Policy Table → DNS Records**. Verify DNS, trusted
TLS, browser pairing, and the projects view before applying the [Homepage
card](../homepage/README.md).
Docker stdout collection is disabled because the upstream foreground server
prints a pairing credential at startup.

## Backup and rollback

Session state, checkouts, and provider sign-ins live in `/srv/state/t3code`.
Before upgrading, stop the container and make a protected backup of that
directory. Restart the prior image if the upgrade has to be deferred. Keep
backup archives private because they contain credentials and session data.

To roll back an upgrade, stop the container, preserve its current state, restore
the pre-upgrade snapshot, and apply the previous compatible release. To remove
the scoped gateway change, reapply Caddy using the Compose files in
`/var/lib/homecompute/t3code-gateway/FULL_SHA/receipt.json` in their recorded
order with `/etc/homecompute/control-plane.env`. Restore the Homepage
configuration recorded in its [rollback procedure](../homepage/README.md) and
remove the T3 Code DNS record only if it still matches the declared value.
