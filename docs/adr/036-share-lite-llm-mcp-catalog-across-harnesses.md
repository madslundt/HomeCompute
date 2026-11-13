# ADR-036: Share the LiteLLM MCP catalog across harnesses

## Status

Accepted; implementation and client migration pending the normal reviewed release.
Supersedes the per-client MCP catalog restriction in [ADR-033](033-litellm-mcp-gateway.md).

## Context

HomeCompute now uses LiteLLM as the shared MCP endpoint for its harnesses. The
owner wants Hermes, OpenBot, OpenClaw, Codex and future harnesses to use the
same configured MCP catalog. OpenBot's MCP list is server-wide, so a connection
added there is available to every OpenBot agent.

The catalog includes public Context7 lookup, read-only Aula and offer search,
the owner n8n MCP with workflow and agent administration tools, and three
HomeCompute relay aliases. The relay aliases require distinct bearer
credentials. The Android alias includes five bounded UI interaction tools in
addition to five read tools.

## Decision

Add the `all-harness-mcps` access group to every MCP server in the LiteLLM
catalog. Assign that group to one dedicated LiteLLM virtual key per harness.
Keep `allow_all_keys: false` and `require_key_mcp_access_defined: true`; model-
only and infrastructure service keys remain assigned `no-mcp-servers`.

The full catalog is available to all agents sharing an enrolled harness. This
includes n8n workflow/agent administration and the bounded Android UI actions.
OpenBot uses one shared server-level connection and therefore exposes those
tools to all OpenBot agents. Each HomeCompute relay alias continues to require
its own role-scoped bearer header; the LiteLLM virtual key does not replace or
merge those principals.

Harnesses retain distinct LiteLLM keys and private configuration. Do not copy
the master key, n8n backend token, or one relay principal's bearer into another
role. Existing clients must be migrated to the private gateway and verified
individually. This decision does not register workstation-only stdio servers,
Codex app connectors, or unreviewed external MCP services.

## Consequences

- One catalog and private endpoint serve each enrolled harness.
- Every enrolled agent can discover and call every catalog MCP reachable with
  that harness's configured relay headers.
- OpenBot's server-wide settings give every OpenBot agent the full catalog.
- LiteLLM key revocation removes a harness's gateway access, while relay bearer
  revocation remains a separate control.
- Connected model providers may receive MCP results when an agent sends them
  through that provider; provider assignment remains a separate client policy.
- Gateway catalog availability and key grants alone do not prove that a backend
  is healthy or that an agent has the required role-scoped relay bearer.

## Rollout

1. Publish and deploy the source change through the Home Core release lifecycle.
2. Deploy the idempotent grant for the known Hermes, OpenBot, and OpenCode
   keys. Assign `all-harness-mcps` to each other intended harness key in
   LiteLLM.
3. Add the LiteLLM endpoint and only the required role-scoped bearer headers to
   each client through its supported secret/configuration path.
4. Verify `tools/list` and a harmless read call per backend and harness. Confirm
   the seven catalog aliases, the exact ten Android tools, and default-denial of
   unrelated model-only service keys.
5. Remove superseded direct MCP registrations only after each gateway client
   passes its authenticated check.

## Evidence

- [ADR-033: LiteLLM MCP gateway](033-litellm-mcp-gateway.md)
- [MCP access inventory](../research/mcp-access-inventory-2026-11-10.md)
- [OpenBot MCP setup](../openbot-android-mcp.md)
