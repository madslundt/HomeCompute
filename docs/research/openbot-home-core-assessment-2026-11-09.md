# OpenBot on Home Core: product and deployment assessment

Assessed: 2026-11-09. HomeCompute source baseline reviewed: `origin/master`
`b1856a6c1f2499f63a7d7b44505c6c93ade038f0`. This is a product-fit review;
OpenBot was not installed and no host configuration was changed.

## Recommendation

OpenBot (`nightly-labs/openbot`) can run headlessly as a self-hosted Linux
server. Its official Docker image supports `linux/amd64`, so it is technically
compatible with Home Core's x86 host. The supported container keeps its agent
runtime inside `/data`, runs as UID 1000 with the Electron sandbox enabled, and
requires a custom seccomp profile and 1 GiB shared memory. Agents nevertheless
have full command, file, and network access inside that container; the container
is the security boundary. Do not mount the Docker socket, host home directories,
or other host data into it. [OpenBot Docker guide](https://github.com/nightly-labs/openbot/blob/main/docs/docker.md)

The latest upstream release inspected on 2026-11-09 is `v0.65.0`. The GHCR
`linux/amd64` manifest digest is
`sha256:e8c0338a76efe51e472ee5a90836d99fb7341924c5148eb1da78d402e693d514`;
the release publishes `OpenBot-0.65.0-linux.spdx.json`. These pins make a
reproducible candidate, not a HomeCompute-qualified deployment.

This makes a controlled Home Core-hosted trial plausible, but does not qualify a
direct deployment on the NixOS host. Prefer evaluating OpenBot as a separate
service in the existing `agents` KVM guest, behind its own restricted network,
storage, and credentials. First verify that this placement can reach only the
approved LiteLLM consumer endpoint and chosen cloud providers, and that the
OpenBot container's own sandbox works under the guest's Docker/NemoClaw
environment. HomeCompute already places prompt-influenced agents in that guest;
the host itself owns trusted gateway services and secrets. [ADR-017](../adr/017-consolidated-application-host.md)
and [ADR-018](../adr/018-multiple-developer-harnesses.md) also require
least-privilege placement and security review for a remotely reachable harness.

## Live readiness gate

Read-only checks on 2026-11-09 found `home-core` deployed at
`481df8e0225c3530e3c0e244e14e8410d1862af1`, while the published desired
revision was `b1856a6c1f2499f63a7d7b44505c6c93ade038f0`. Two systemd units were
failed: `homecompute-assistant-context-sync.service` and
`homecompute-platform-health.service`. Reconcile this known drift and resolve
the health failures before any new workload rollout. The context-sync journal
reports an Aula-source error; Home Assistant, Calendar, and Daythread completed
that run. The platform-health probe found its edge, n8n, speech, and model
consumer checks healthy, but `homecompute_nas_backup_ready` is `0`, so the
platform probe exits failed. No service was modified. This outstanding backup
readiness is also relevant to OpenBot's durable workspace data.

## Product fit

OpenBot provides persistent named agents with separate workspaces, queues,
handoffs, file sharing, and channels. It supports Ollama/LM Studio detection
and custom OpenAI-compatible endpoints alongside several cloud providers and
ACP agents. This matches the local-plus-cloud team concept. Cloud providers
receive the prompts and files sent to them, so cloud-model use needs an explicit
per-agent data policy. [README](https://github.com/nightly-labs/openbot#what-works)
and [privacy boundaries](https://github.com/nightly-labs/openbot/blob/main/PRIVACY.md)
describe the provider and data paths.

The no-screen server is a real supported mode: upstream provides an Ubuntu
24.04/26.04 systemd installer and a Docker image. The Docker image publishes no
port and has no bundled web UI; it connects to OpenBot's account, Signal, and
TURN services, and users connect from the desktop/iPhone app or `openbot.run/app`.
The basic deployment therefore depends on OpenBot's hosted account/connectivity
services. [Self-hosted server guide](https://github.com/nightly-labs/openbot/blob/main/docs/self-hosted-server.md)
and [Docker guide](https://github.com/nightly-labs/openbot/blob/main/docs/docker.md)
document this flow.

The Docker guide's connectivity is a material constraint for this host. Remote
clients use WebRTC directly or through TURN. OpenBot documents Signal over HTTPS
and WebSocket, and WebRTC to the peer or TURN relay over the UDP/TCP ports named
by Signal. The self-hosted Signal/TURN stack documents TCP 443, TCP/UDP 3478,
TCP 5349, and UDP 49152–65535 for its public listener. HomeCompute currently
permits only outbound TCP 80/443 from the agents VM, so the remote connection
path is not qualified. Do not switch the OpenBot container to host networking;
upstream warns that it removes network isolation and lets agents reach guest
loopback services. [OpenBot network notes](https://github.com/nightly-labs/openbot/blob/main/docs/docker.md#network),
[OpenBot remote production requirements](https://github.com/nightly-labs/openbot/blob/main/remote/README.md#production-requirements),
[HomeCompute agents VM policy](../adr/017-consolidated-application-host.md)

Replacing those services is a much larger setup: upstream requires a Cloudflare
Workers/D1/R2 account API, its own ticket keys, Signal/TURN, a Cloudflare domain,
SMTP, and a Linux server with a static public IPv4 address. The self-hosted
account mode does not work with `openbot.run/app`; all clients must use that
service, and several hosted integrations remain unavailable. That path does not
fit HomeCompute's private-network policy without a separately reviewed redesign.
[Self-hosted remote access guide](https://github.com/nightly-labs/openbot/blob/main/docs/self-hosting.md)

## HomeCompute gates

- Keep the LiteLLM boundary: one dedicated revocable consumer key, local-only
  route for private data, and explicit provider assignment. Do not give OpenBot
  a gateway master/admin credential or silently enable cloud fallback. [ADR-020](../adr/020-workflow-first-local-inference.md)
  and [ADR-003](../adr/003-ai-api-boundary.md).
- Keep the container on Docker bridge networking. Qualify an explicit narrow
  egress exception for its exact LiteLLM compatibility endpoint and OpenBot's
  required TURN path; the current TCP-only host policy blocks the latter. Do
  not widen egress to all agents VM workloads or all public UDP destinations.
- Keep OpenBot and agent workspaces out of the NixOS host trust domain. The
  Docker guide warns that host networking removes the container's network
  isolation and lets agents reach host loopback services. Its multi-instance
  firewall instead blocks private/local addresses, which would also prevent a
  local LiteLLM connection; the chosen network must be verified rather than
  bypassed with host networking. [Docker network and security notes](https://github.com/nightly-labs/openbot/blob/main/docs/docker.md#network).
- Pin a release image by digest, retain its provenance attestation, declare
  resource limits and durable `/data` backup/restore, and add the service to the
  owning lifecycle and monitoring. OpenBot's one-container example uses
  `latest`; HomeCompute requires reproducible tracked pins and explicit backup
  ownership. [Docker operations](https://github.com/nightly-labs/openbot/blob/main/docs/docker.md#updates).
- OpenBot is PolyForm Noncommercial 1.0.0 in current releases. Personal
  noncommercial use is permitted; commercial use requires a separate license.
  [License](https://github.com/nightly-labs/openbot/blob/main/LICENSE).

## Decision and implementation status

The deployment source now defines OpenBot in the agents VM under
[`deploy/openbot/`](../../deploy/openbot/README.md), pins the `v0.65.0` image
and seccomp profile, and restricts the container to the host LiteLLM endpoint,
public TCP 80/443, and the exact currently resolved TURN relays. Home Core's
agents-VM firewall adds only those relay IP/port rules. A dedicated key
provisioner scopes the local route to `automation-moe`.

This source has not yet been deployed or runtime-qualified. Deployment still
needs the Home Core release lifecycle; remote WebRTC, account sign-in, the local
LiteLLM request path, cloud-provider sign-in, and restart/backup behavior remain
unverified on this host. The owner must complete OpenBot sign-in and select the
cloud provider and agent data policy.
