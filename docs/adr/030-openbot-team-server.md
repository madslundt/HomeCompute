# ADR-030: Run OpenBot as an owner-managed team server

## Context

OpenBot provides persistent, named agents and teams, with local OpenAI-compatible
providers and cloud providers. Its headless Linux server can run in Docker, but
its agents can execute commands and access files and networks inside the
container. The container needs outbound access to OpenBot's hosted account,
Signal, TURN, and user-selected model providers. Team workspaces, credentials,
and the OpenBot keyring persist together under `/data`.

Home Core already runs an isolated Ubuntu agents VM for household harnesses.
The VM blocks private-network access by default, permits reviewed internet
egress, and is covered by an off-host backup and restore gate. Its Docker bridge
can reach the existing host-only LiteLLM compatibility endpoint on port 18080.

## Decision

Install one digest-pinned OpenBot server in the existing agents VM. Keep the
container on its own user-defined Docker bridge with no host ports, Docker
socket, host networking, or host-data mounts. Track the upstream seccomp profile,
drop Linux capabilities, and bound memory, CPU, processes, shared memory, and
Docker logs. Let a dedicated systemd service start it only after its
container-specific firewall is active.

The container may reach public TCP 80/443, the exact LiteLLM compatibility
endpoint, and the current OpenBot TURN IPv4 relays on their documented listener
and allocation ports. Block all other private and public UDP destinations.
Restrict Home Core's agent-VM egress to the same exact TURN addresses. Review
and refresh those addresses from DNS before changing the network declaration;
stale entries fail closed.

Use a dedicated non-expiring LiteLLM consumer key restricted to the local-only
`automation-moe` alias. Cloud providers are connected by the owner in OpenBot
and assigned deliberately to agents. No cloud fallback is added to the local
route. The owner is responsible for deciding which prompts and files may reach
each cloud provider and for creating agents and teams in the application.

### Model alias access amendment (2026-11-12)

The owner may select the configured ChatGPT subscription routes through LiteLLM
for OpenBot agents. Keep a separate non-expiring key for this provider, limited
to `chatgpt-sol-latest`, `chatgpt-terra-latest`, and `chatgpt-luna-latest`, with
MCP access denied. The existing `automation-moe` key and provider remain
separate. Configure the OpenBot custom provider at
`https://mcp.home.arpa/v1`; the three aliases become available to the owner,
while agent assignment remains deliberate. The initial assignment is Researcher
to `chatgpt-sol-latest`; Terra and Luna remain selectable without changing other
agents. LiteLLM retains the ChatGPT OAuth material; OpenBot receives only its
scoped virtual key. The existing public/cleared-data rule for cloud agents still
applies.

Use OpenBot's hosted account, Signal, and TURN services. Do not deploy its
self-hosted Cloudflare account stack or publish ports from Home Core. Account
sign-in and cloud-provider authentication are owner actions.

## Consequences

Remote WebRTC depends on OpenBot's hosted TURN service and its current IP
addresses. The guest service is checked through its dedicated setup helper;
Home Core's Docker inventory does not see containers inside the VM. The full
VM disk is the durable backup unit. Confirm a fresh completed off-host archive
before introducing household workspaces or provider credentials.

This arrangement isolates OpenBot from the NixOS host and unrelated guest
services at the Docker egress boundary. It does not prevent an owner-configured
cloud provider from receiving assigned agent prompts or files.

## Alternatives

- Run OpenBot on the NixOS host: rejected because agent commands would share the
  trusted service host.
- Use Docker host networking: rejected because it removes container network
  isolation and exposes guest loopback services to agents.
- Self-host OpenBot's account and Signal/TURN services: rejected because that
  requires public infrastructure and a separate Cloudflare service deployment.
- Route private data through a cloud fallback: rejected because local-only
  routing is an explicit HomeCompute policy.

## Status

Accepted for the pinned Home Core deployment; remote client connectivity and
model routing remain subject to live verification after owner sign-in.

## Evidence

- [Home Core assessment](../research/openbot-home-core-assessment-2026-11-09.md)
- [OpenBot operations](../../deploy/openbot/README.md)
- [OpenBot Docker guide](https://github.com/nightly-labs/openbot/blob/v0.65.0/docs/docker.md)
