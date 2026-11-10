# ADR-033: Use LiteLLM as the central MCP gateway

Status: Accepted; implementation and rollout pending the normal reviewed release.

Date: 2026-11-10

## Context

Home Core already runs LiteLLM with PostgreSQL-backed virtual keys and a private
Caddy front door. n8n, OpenBot, Hermes, Codex, OpenClaw, and other MCP clients
should point at one MCP endpoint. Server registration and tool permissions
should be managed centrally, with separate grants for each client.

The inventory includes local Aula and TilbudsTrolden, the n8n instance MCP,
hosted Context7, and separate role-scoped paths through the HomeCompute
capability relay. The Android principal exposes five read tools and five bounded
UI interaction tools. n8n still has direct saved workflow connections, and
agent registrations remain on their existing paths until each client is
migrated and verified.

## Decision

Use the existing LiteLLM Proxy MCP Gateway on `home-core`. Pin the reviewed
LiteLLM image in `config/control-plane.env.example`, register the MCP catalog in
`deploy/control-plane/litellm-config.yaml`, and serve it at the private
`https://mcp.home.arpa/mcp` endpoint through Caddy. The LiteLLM Admin UI remains
available on that private hostname for server, key, team, and tool grants.

Each server alias belongs to its own access group: `aula-read`, `offers-read`,
`docs-public`, `n8n-owner`, `hermes-owner-tools`, `openclaw-agent-tools`, and
`openbot-android-bounded-ui`. They are not public to every LiteLLM key. The
n8n catalog is restricted to the 55 tools observed from its authenticated live
endpoint. The Android alias is restricted to its ten reviewed tablet tools;
the five interaction tools remain governed by the existing bounded-action
policy. Because LiteLLM's default is open when no entity has an MCP permission
list, the rollout must mark every existing unscoped virtual key
`no-mcp-servers` before publishing the MCP route; generated service keys carry
that sentinel as well. Require explicit key-level MCP grants, disable
prompts/resources, and keep only selected upstream paths reachable from
LiteLLM. Do not grant Aula, n8n-owner, Hermes-owner, or Android tools to
OpenBot's shared agent list by default.

The tracked file defines the server catalog; LiteLLM's database owns dynamic
virtual keys and per-client grants. Each client is configured once with the
gateway URL and its own LiteLLM credential. Existing saved workflow and agent
MCP clients are not rewritten by this source change. n8n's direct backend path
remains as a compatibility lane until its saved credentials and workflows are
migrated to LiteLLM; the gateway is not the exclusive ingress until that
cutover is complete.

## Consequences

- No second MCP gateway process, credential store, or database is needed.
- LiteLLM's existing key/team controls can scope the servers and individual
  tools. Adding a server in the catalog does not itself grant a client access.
- n8n's current direct MCP workflows remain operational during the staged
  cutover. Remove the legacy migration-network path only after those workflows
  use separate LiteLLM keys and pass the authenticated gateway checks.
- Upgrading LiteLLM must preserve existing virtual keys and pass the documented
  PostgreSQL backup and migration checks.
- Caddy's `mcp.home.arpa` route is private and authenticated by LiteLLM. Its
  certificate uses the existing Home Core local CA.
- If independent MCP availability, storage, or release cadence becomes a
  requirement, reconsider a dedicated gateway such as MCPHub in a new ADR.

## Alternatives considered

### MCPHub

MCPHub provides a dedicated server registry and operator UI. It would create a
second service and state database on Home Core. LiteLLM already supplies the
shared endpoint, virtual-key database, and access management needed here, so
the additional control plane is not justified for the current inventory.

### Direct MCP configuration in each client

This duplicates server URLs and upstream credentials across clients and does
not provide one place to revoke or filter access. It does not meet the goal.
