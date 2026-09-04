# NixOS control-plane workload

This is the deliberately small first stack for `home-core`: Caddy, one
LiteLLM process, and a PostgreSQL instance dedicated to LiteLLM.

## Why this split

| NixOS host | This Compose project | Separate Compose projects, same host | Separate host |
| --- | --- | --- | --- |
| SSH, time sync, updates, disk health, networking, firewall, backup transport, Docker Engine, sops-nix secrets, `/srv/state` | Caddy, one LiteLLM worker, dedicated PostgreSQL | n8n, browser workers, agent/code sandboxes, toolbox builds | Home Assistant, GB10 inference |

Redis is deferred entirely, not relocated; see below.

The third column shares this host's kernel. [ADR-017](../../docs/adr/017-consolidated-application-host.md)
accepts that because no fourth machine exists. Those projects must not join the
`internal` network defined here, must not mount the Docker socket, and must
read their secrets from a different sops group — otherwise the split in this
table is a naming convention rather than a boundary.

PostgreSQL is present because independently revocable LiteLLM virtual keys are
a day-one requirement. LiteLLM's bootstrap database account is separate from
its non-superuser application owner. Redis is not needed for one worker and
would otherwise introduce another credential and prompt/response retention
surface. Add a LiteLLM-dedicated Redis/Valkey only before enabling multiple
workers/replicas or after explicitly approving response caching and retention.

Caddy remains in this project while it fronts only this gateway. The official
image starts as root, but it listens on unprivileged container port 8443, has
only its binary-required `NET_BIND_SERVICE` capability, has a read-only root filesystem, and can write
only its named data/config volumes. A Docker socket, host network, privileged
mode, devices, and broad host bind mounts are absent. LiteLLM uses the signed
upstream non-root image family. PostgreSQL starts directly as Alpine UID/GID 70 with all capabilities removed.
Its state directory must be owned by 70:70; NixOS tmpfiles provisions this.
Starting directly preserves supplementary group 989 for reading SOPS secrets;
the official root entrypoint would discard that group when switching users.

## Network and transport policy

Only Caddy publishes a host port: TCP 443 on the exact IPv4 address in
`CONTROL_PLANE_BIND_ADDRESS`. There is no TCP 80 or UDP 443 in the first
deployment. Wildcard IPv4 and IPv6 publications are forbidden.

- `edge` contains Caddy and LiteLLM. It is the sole egress-capable application
  bridge; LiteLLM is given its default route there.
- `state` contains LiteLLM and PostgreSQL, is `internal`, and uses Docker's
  isolated gateway mode. PostgreSQL has no host publication. Caddy cannot join
  this network.
- LiteLLM listens on `0.0.0.0:4000` only inside its container namespace. This
  narrow exception is necessary for Caddy to reach it and is not a host bind.

These networks are coarse membership controls, not per-port or egress policy.
The installer requires Docker Engine 28+ and Compose 2.33.1+. It also sets the
daemon-wide default bind for future user-defined bridge publications to
`127.0.0.1` as a fail-safe.

Start on `127.0.0.1`. Before using a LAN or Tailscale address, declare the exact
ingress policy in `modules/nixos/firewall.nix`, rebuild NixOS, and verify the
result from both allowed and denied clients. Docker-published traffic requires
an explicit verification because it does not have the same filtering behavior
as an ordinary host process.

LiteLLM-to-compute traffic is HTTPS by default. Plain HTTP exposes bearer
credentials and prompts, so it is accepted only with
an explicit documented exception on a dedicated, non-routed point-to-point
link or VLAN. Prefer a compute certificate trusted by the LiteLLM image or a
mutually authenticated tunnel.

## Live deployment

The K15 deployment is recorded in [control-plane-deployment.md](../../docs/control-plane-deployment.md).
Its gateway uses `home-core.tail479ad.ts.net` on the Tailscale address, and
`/etc/homecompute/control-plane.env` holds the resolved production settings.
Backups remain deferred by the operator. Model aliases are configured for the
GB10, which is not connected yet; gateway health does not establish inference
availability.

## Installation

```bash
sudo nixos-rebuild build --flake .#home-core
sudo nixos-rebuild switch --flake .#home-core
sudo docker compose --env-file config/control-plane.env.example \
  -f deploy/control-plane/compose.yaml config --quiet
sudo docker compose --env-file config/control-plane.env.example \
  -f deploy/control-plane/compose.yaml up -d
```

The runtime environment file contains only deployment settings and paths.
sops-nix creates the root-owned secret files with group `homecompute-secrets`
and mode `0440`; the fixed group ID is passed to the non-root LiteLLM container.

Use fixed semantic-version tags plus reviewed manifest digests. The validator
allow-lists Docker Official Caddy/PostgreSQL repositories and LiteLLM's signed
GHCR non-root repository; moving `latest` or `main-stable` tags are rejected.
The deployment manifest records the rendered Compose hash, every mounted
artifact hash, runtime versions, requested image references, and actual
container image IDs.

## TLS, keys, and state

`ai.home.arpa` uses Caddy's internal CA. The smoke test reads that CA and uses
normal certificate validation; it never uses `curl --insecure`. Install the
root only on approved clients. The online CA private key is in `caddy-data`, so
compromise can mint trusted certificates; encrypt its off-host backup and
limit client trust to devices that need this service.

Caddy exposes only `/v1/*` and `/healthz`. It intentionally returns 404 for the
LiteLLM admin UI and key-management API. Provision and revoke virtual keys from
an administrator shell inside LiteLLM's container namespace; do not distribute
the master key to clients or publish the management route for convenience.

Back up and restore-test `/srv/state/control-plane` together with the persistent
sops age identity before production use. Never change the LiteLLM salt after
virtual keys exist. The NixOS backup module remains disabled until its real
off-host repository and runtime password path are configured. Image updates
are manual and require backup plus release-note and vulnerability review; no
automatic container updater is installed.
