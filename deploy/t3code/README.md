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

Keep this tunnel open while using T3 Code:

```sh
ssh -N -o ExitOnForwardFailure=yes -L 127.0.0.1:3773:127.0.0.1:3773 home-core
```

In a second terminal, create a five-minute, single-use pairing link:

```sh
ssh home-core 'sudo docker exec homecompute-t3code-t3code-1 t3 auth pairing create --ttl 5m --base-url http://127.0.0.1:3773'
```

Open the printed link in your browser. Future visits use
[http://127.0.0.1:3773](http://127.0.0.1:3773) with the paired browser and an
open tunnel. Generate a new link for each client. Pairing links are credentials.
Manage established sessions under **Settings → Sharing & access**.

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

Verify client access by pairing through the SSH tunnel and opening the projects
view. Docker stdout collection is disabled because the upstream foreground
server prints a pairing credential at startup.

## Backup and rollback

Session state, checkouts, and provider sign-ins live in `/srv/state/t3code`.
Before upgrading, stop the container and make a protected backup of that
directory. Restart the prior image if the upgrade has to be deferred. Keep
backup archives private because they contain credentials and session data.

To roll back an upgrade, stop the container, preserve its current state, restore
the pre-upgrade snapshot, and apply the previous compatible release with its
installer. To roll back the first installation while retaining all data, use
the active release and its full revision:

```sh
sudo env T3CODE_RELEASE="$revision" docker compose -f "$release/deploy/t3code/compose.yaml" down
```
