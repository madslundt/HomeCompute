# ADR-032: Host a central MCP gateway on Home Core

## Context

HomeCompute MCP servers are configured independently in workflow automation and
agent runtimes. This duplicates server setup and tool policy across clients,
and makes it difficult to review which client can reach which tools. The
gateway must preserve the local-first trust model and must not become a general
container or host administration surface.

## Decision

Host MCPHub on `home-core` as the shared MCP entry point. Pin its immutable
container image in `config/control-plane.env.example`; store its database in the
existing control-plane PostgreSQL cluster under a dedicated role and database.
Keep its credential-encryption key and initial administrator password in a
dedicated SOPS document. Back up the database through the existing full-cluster
PostgreSQL archive and retain the encryption key with the deployment secrets.

Run the gateway without a Docker socket, a host-published port, or public
egress; keep the source-declared backends on HTTP transports. Use separate
internal Docker networks for n8n clients, the two current HTTP MCP backends,
Caddy, and the database. The NixOS Docker policy permits
only MCPHub to initiate connections to the fixed unauthenticated Aula and
TilbudsTrolden backend endpoints. Caddy publishes the dashboard and MCP HTTP
routes through the existing private HTTPS edge. MCP bearer authentication is
required, the unscoped global route is disabled, upstream Authorization
passthrough stays disabled, and stored tool-call payloads are disabled.

Keep server declarations in the repository and reconcile them to PostgreSQL
with MCPHub `upsert` mode. Keep users, bearer keys, credentials, and other
runtime state in PostgreSQL. Do not create a shared all-tools client key.
Review the discovered tool inventory and declare per-client groups and scoped
keys before connecting additional clients. OpenBot's MCP server list is shared
among its agents; do not grant private HomeCompute tools to that shared list.

## Alternatives

- Configure each MCP server separately in n8n, Codex, Hermes, OpenClaw and
  OpenBot: rejected because server and tool policy would remain duplicated.
- Extend LiteLLM as the MCP gateway: rejected for the first rollout because it
  couples MCP tool access to the model gateway's lifecycle and security
  surface.
- Run Docker MCP Gateway: rejected because its orchestration model would grant
  a control surface over Docker-managed MCP containers.
- Use a gateway with weaker client or tool scoping: rejected because each
  client needs a distinct reviewed grant.

## Consequences

MCPHub becomes a durable Home Core application with a PostgreSQL database and a
separately backed-up encryption key. An outage removes the unified MCP path;
clients that are later migrated must use the gateway rather than retain direct
backend routes. Initial deployment registers only the existing Aula and
TilbudsTrolden streamable HTTP services. Client-specific groups, scoped keys,
and consumer migrations require discovered-tool review before activation.

## Status

Accepted; implementation is being prepared for the guarded Home Core release.
Live deployment and authenticated client-path verification are tracked
separately from source validation.

## Evidence

- [MCP gateway evaluation](../research/mcp-gateway-evaluation-2026-11-10.md)
- [MCPHub v1.2.1 release](https://github.com/samanhappy/mcphub/releases/tag/v1.2.1)
- [MCPHub database configuration](https://github.com/samanhappy/mcphub/blob/v1.2.1/docs/configuration/database-configuration.mdx)
- [MCPHub HTTP endpoints](https://github.com/samanhappy/mcphub/blob/v1.2.1/docs/api-reference/mcp-http.mdx)
- [HomeCompute security policy](../../SECURITY.md)
