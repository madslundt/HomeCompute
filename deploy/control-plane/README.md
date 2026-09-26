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

Only Caddy publishes a host port in the default profile: TCP 443 on the exact
IPv4 addresses in the control-plane environment. The optional speech profile
adds only the two Home Assistant-restricted Wyoming ports described below.
There is no TCP 80 or UDP 443. Wildcard IPv4 and IPv6 publications are
forbidden.

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

Start on `127.0.0.1`. The production Tailscale publication is paired with the
repository-owned `HC-CADDY-INGRESS` policy in
`modules/nixos/automation-network.nix`: Docker's original destination
`100.110.248.102:8443` is accepted only from `tailscale0` and rejected from every
other forwarded ingress path. Rebuild NixOS and verify both allowed and denied
clients whenever the address or interface changes.

LiteLLM-to-compute traffic is plain HTTP only on the dedicated, non-routed
point-to-point link. The versioned `HC-COMPUTE` policy permits the fixed
LiteLLM container address to six OpenAI-compatible compute ports. Two separate,
fixed relay addresses may reach only Plapre Wyoming port 10201 and Hviske
Wyoming port 10301; every other forwarded flow is rejected. Host and container-forwarded traffic
to the compute subnet is rejected unless it leaves the dedicated interface,
preventing bearer credentials from falling back through the LAN default route
while the cable is down. A routed
replacement must use a compute certificate trusted by LiteLLM or a mutually
authenticated tunnel.

### Opt-in SSH fallback while the private NIC is down

The dedicated `enp45s0` link remains the preferred production transport. When
that physical path is unavailable, `modules/nixos/compute-ssh-tunnel.nix`
provides a deliberately opt-in fallback over the trusted management network.
It forwards only these `home-spark` loopback listeners:

| Edge client | Host-side tunnel endpoint | Remote loopback destination |
| --- | --- | --- |
| LiteLLM `172.28.200.3` | `172.28.200.1:18005` | `127.0.0.1:8005` (automation MoE) |
| Plapre relay `172.28.200.4` | `172.28.200.1:18201` | `127.0.0.1:10201` |
| Hviske relay `172.28.200.5` | `172.28.200.1:18301` | `127.0.0.1:10301` |

No primary text, embedding, vision, legacy STT, or legacy TTS port is carried
by this fallback. The host INPUT chain permits each local port only from its
named container address and rejects every other source. SSH uses
`BatchMode`, `ExitOnForwardFailure`, strict pinned-host-key checking, and a
15-second server-alive interval. It restarts persistently but never falls back
to passwords, an agent, or an unpinned host key.

Provision the credentials without copying a private key into Git or the Nix
store:

1. On `home-core`, create `/etc/homecompute/compute-tunnel` as root mode `0700`.
   Generate a dedicated Ed25519 key at
   `/etc/homecompute/compute-tunnel/id_ed25519`; the private key must be
   `root:root` mode `0400`.
2. At the `home-spark` console, create the unprivileged
   `homecompute-tunnel` account. Add only the generated public key to its
   `authorized_keys`, prefixed with:

   ```text
   restrict,port-forwarding,permitopen="127.0.0.1:8005",permitopen="127.0.0.1:10201",permitopen="127.0.0.1:10301"
   ```

   The `home-spark` host firewall must admit SSH for this account only from
   `192.168.30.122`; no inference listener is moved off loopback.
3. Record the `home-spark` host key for `192.168.30.126` in
   `/etc/homecompute/compute-tunnel/known_hosts`. Verify its fingerprint at the
   physical console before trusting it. Keep the file root-owned and not
   group/world writable.
4. Set `homecompute.computeSshTunnel.enable = true` in the `home-core` host
   configuration, build, inspect the diff, and switch. This single option
   starts the persistent service and rewrites only the automation, Plapre, and
   Hviske upstreams in `/etc/homecompute/control-plane.env` to the host-side
   ports above. The checked-in environment template remains on
   `COMPUTE_TRANSPORT=dedicated-link`.
5. Recreate LiteLLM and, when ready, the optional speech relays using the
   regenerated environment file. Verify positive access from `.3`, `.4`, and
   `.5`, and negative access from every other edge container before promoting
   any route.

To return to the preferred cable, stop the speech relays, set the option back
to `false`, rebuild, and recreate the affected containers. Confirm direct
`10.77.10.10` health before removing the dedicated tunnel account or key. Do
not run both transports under the same semantic route during cutover.

## Live deployment

The K15 deployment is recorded in [control-plane-deployment.md](../../docs/control-plane-deployment.md).
Its gateway uses `home-core.tail479ad.ts.net` on the Tailscale address, and
`/etc/homecompute/control-plane.env` holds the resolved production settings.
Backups remain deferred by the operator. Model aliases are configured for the
GB10, which is not connected yet; gateway health does not establish inference
availability.

The gateway has local backends only. `auto` is initially another served name
for the single resident workhorse, not a prompt classifier. The gateway never
holds a cloud-provider credential or performs cloud fallback; an approved
caller must select any separate cloud path before assembling private context.

### On-demand automation standby

The optional `automation-backup` profile runs the pinned Unsloth
Qwen3.6-35B-A3B `UD-Q4_K_M` GGUF with CPU-only llama.cpp on `home-core`.
It has no published port and shares only an isolated internal network with
LiteLLM. Both deployments belong to the `automation` model group. The normal
gateway config prefers Spark; `maintenance-start` warms and tests home-core,
then briefly recreates only LiteLLM with an alternate config that explicitly
prefers the CPU standby. This avoids waiting for a dead-but-connected Spark
tunnel during a planned swap.

The model and image stay cached, but the process is stopped during normal
operation so it does not reserve roughly 28 GiB of memory. Before a planned
Spark model swap, run:

```bash
sudo /srv/homecompute/current/scripts/setup-home-core-automation-backup.sh prepare
sudo /srv/homecompute/current/scripts/setup-home-core-automation-backup.sh maintenance-start
```

Confirm no automation request is active before running `maintenance-start`,
because the gateway recreation can interrupt an in-flight model request. Do
not stop the Spark endpoint until the command reports that its direct Danish
content/tool-call smokes and the stable `automation` alias smoke all passed.
Perform the swap, then verify the new Spark endpoint. Restore normal routing
and release the CPU model's RAM afterward:

```bash
sudo /srv/homecompute/current/scripts/setup-home-core-automation-backup.sh maintenance-stop
```

When stopped, this is a cold cached recovery path rather than instant failover.
An unexpected Spark outage can still produce failures until an operator starts
the standby. Never retry an entire n8n workflow after it may have performed a
write; retry only the model step, and keep write steps idempotent.

## Installation

```bash
sudo nixos-rebuild build --flake .#home-core
sudo nixos-rebuild switch --flake .#home-core
sudo docker compose --env-file config/control-plane.env.example \
  -f deploy/control-plane/compose.yaml config --quiet
sudo docker compose --env-file config/control-plane.env.example \
  -f deploy/control-plane/compose.yaml up -d
```

The optional `speech-proxies` profile stages the two unauthenticated Wyoming
streams on the LAN-facing `home-core` address. It is deliberately absent from
normal deployment. Start it only after the private cable, both compute services,
the `HC-COMPUTE-V2` policy, and the Home Assistant-only ingress rules are
healthy:

```bash
sudo docker compose --profile speech-proxies \
  --env-file /etc/homecompute/control-plane.env \
  -f /srv/homecompute/current/deploy/control-plane/compose.yaml \
  up -d --wait plapre-wyoming-proxy hviske-wyoming-proxy
```

The intended Home Assistant endpoints are `192.168.30.122:10201` for Plapre
TTS and `192.168.30.122:10301` for Hviske STT. The relays parse and log no
speech payloads, fail health checks when their upstream is unavailable, and
are restricted at the Docker ingress boundary to the Home Assistant appliance
at `192.168.30.30`. Piper `:10200` and Faster Whisper `:10300` remain the
rollback services.

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
Embedding, vision, STT, and TTS candidates are also absent from the production
LiteLLM model list and Caddy route allow-list while staged. Qualify one modality
at a time against its direct private listener; add a gateway alias only through
an explicit promotion change with route, quota, monitoring, and rollback tests.

Back up and restore-test `/srv/state/control-plane` together with the persistent
sops age identity before production use. Never change the LiteLLM salt after
virtual keys exist. The NixOS backup module remains disabled until its real
off-host repository and runtime password path are configured. Image updates
are manual and require backup plus release-note and vulnerability review; no
automatic container updater is installed.
