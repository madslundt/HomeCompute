# MCP gateway evaluation

**Verified:** 2026-11-10

**Scope:** One self-hosted MCP access point for HomeCompute clients on `home-core`

**Status:** Candidate recommendation; no software or live configuration changed

## Recommendation

Pilot MCPHub on `home-core` as the MCP control plane. Keep its upstream list and
client policy in reviewed repository configuration, with secrets and bearer
keys provisioned externally. Expose one private Streamable HTTP origin and use
separate named routes and revocable credentials per consumer. Do not start by
placing every MCP server inside the gateway container.

This centralizes the *upstream server configuration* so each client points to
MCPHub once. Each client still needs a gateway URL, transport setting, and its
own scoped credential. A gateway cannot remove that one-time client binding.
MCPHub documents aggregate, group, and per-server routes, scoped bearer keys,
and support for HTTP, SSE, and stdio upstream servers ([MCPHub feature overview](https://www.mcphub.app/features), [quick start](https://docs.mcphub.app/quickstart), [authentication](https://docs.mcphub.app/features/authentication)).

MCPHub is a good functional fit, not an automatic trust-boundary solution. It
would hold or relay access to multiple existing services, so its runtime and
network policy become high-trust infrastructure. The current repository keeps
automation MCPs and agent sandboxes in separate network domains, and the
OpenBot install currently shares MCP connections server-wide rather than
granting them per agent. Keep the owner/Home Assistant, automation, and
OpenBot/shared-agent authorities separate until exact caller and tool grants
are proven ([ADR-017](../adr/017-consolidated-application-host.md),
[ADR-015](../adr/015-personal-data-domains-and-memory.md),
[OpenBot agent setup](../openbot-agent-setup.md)).

## Candidate comparison

| Candidate | Fit for this use | Main trade-off | Assessment |
| --- | --- | --- | --- |
| **MCPHub** | Self-hosted dashboard, one Streamable HTTP endpoint, server/group routes, per-key scopes, user visibility, upstream credential bindings, health/activity views; supports declarations synced from a settings file in database mode. | New high-trust service and database state; upstream has published authorization/security fixes, so pin a reviewed release and test scope enforcement. Dashboard and DB-held identity/key state still need backup and recovery. | **Best pilot candidate.** Its route/key model and documented GitOps reconciliation fit a small, source-managed deployment. |
| **Existing LiteLLM Proxy MCP gateway** | Already part of the HomeCompute control plane; official docs describe MCP servers and per-key/team/user server and tool permissions. Reusing it avoids a second service. | Couples MCP credential/control policy to the model gateway and its admin/database lifecycle. Requires checking the exact deployed version, current MCP behavior, and adding server declarations to the existing controlled lifecycle. Recent [LiteLLM security advisories](https://github.com/BerriAI/litellm/security/advisories) make version and admin-surface review mandatory. | **Strong alternative** if the installed version and current security review qualify. Compare first in the pilot instead of assuming its MCP endpoint is ready. |
| **Docker MCP Gateway** | Runs packaged MCP servers in isolated containers, supports server/tool allow-lists and stdio, SSE, or streaming client transports. | Its normal model is a local Docker-managed gateway. Managing child server containers requires Docker orchestration authority; server/catalog/profile state and client authentication need a separate HomeCompute integration. | Useful for an operator workstation or curated local catalog; less direct as a shared, scoped home-core service. |
| **IBM ContextForge** | Broad federation, registry, API gateway, and OAuth/team features; container image and SQL-backed deployment are available. | Project security policy says the Admin UI/API are development conveniences that should be disabled in production and that the project has no official IBM support. The current feature surface is larger than this household need. | Not preferred for this deployment. |
| **MCPJungle** | Self-hosted registry/proxy with endpoint tokens and optional tool groups. | Smaller feature set for identity/OAuth and operational integration; its official README says it does not support OAuth. | Worth revisiting if a simpler bearer-token registry is preferred, but MCPHub maps more directly to the requested control dashboard. |

Sources: [LiteLLM MCP overview](https://docs.litellm.ai/docs/mcp/), [LiteLLM MCP access management](https://docs.litellm.ai/docs/proxy/access_management/mcp), [Docker MCP Gateway](https://github.com/docker/mcp-gateway/blob/main/docs/mcp-gateway.md), [ContextForge container deployment](https://github.com/IBM/mcp-context-forge/blob/main/docs/docs/deployment/container.md), [ContextForge security policy](https://github.com/IBM/mcp-context-forge/blob/main/SECURITY.md), [MCPJungle](https://github.com/mcpjungle/MCPJungle).

## MCPHub fit and operating model

At the time of this review, MCPHub's latest visible release is v1.2.1. It is
Apache-2.0 licensed and supports scoped bearer keys for groups or servers,
private/group/public server visibility, and explicit subsets of tools,
prompts, and resources. A group can expose selected tools; a key can be limited
to selected groups or servers. Do not use the aggregate `/mcp` route as the
default client route ([MCPHub repository](https://github.com/samanhappy/mcphub),
[authentication and visibility](https://docs.mcphub.app/features/authentication),
[group tool selection](https://docs.mcphub.app/features/group-management)).

For durable configuration, MCPHub supports PostgreSQL plus startup sync from a
read-only `mcp_settings.json`. `MCPHUB_SETTINGS_SYNC=upsert` makes declared
server/group settings authoritative on startup while leaving users, bearer
keys, personal credentials, and OAuth runtime state in the database. The
optional `prune` mode deletes managed records and associated credentials; do
not use it for the initial pilot. This fits the repository's source-of-truth
rule if non-secret endpoint definitions and tool/group selections are in Git,
while passwords, bearer keys, database credentials, and credential-encryption
keys remain in the external secret lifecycle ([MCPHub database/GitOps guide](https://docs.mcphub.app/configuration/database-configuration)).

Use HTTP-only upstream definitions in the first deployment. MCPHub's default
image includes Node, Python, package managers, and build tools; its `latest-full`
image adds Docker Engine and browser runtimes. Running arbitrary downloaded
stdio processes from a shared gateway would enlarge its code execution and
network authority. Continue to build and pin individual MCP workload images
through HomeCompute's existing setup scripts, then proxy those named internal
HTTP endpoints through MCPHub. Do not mount the Docker socket into MCPHub.

MCPHub documents historical high/critical fixes in authorization and transport
handling. For example, a server-scoped key could gain access to a whole group
through partial matching in releases before 1.0.31, and an unauthenticated SSE
identity issue was fixed before 0.12.15. Both fixes predate v1.2.1, but they
demonstrate why this service must be treated like an identity gateway: pin the
current reviewed image by digest, track upstream advisories, test negative
authorization cases, disable unused transports/features, and keep the dashboard
on the operator-only path. MCPHub's health endpoint is unauthenticated, so
allow only the monitoring path to reach it. ([GHSA-454m-4vm6-842f](https://github.com/samanhappy/mcphub/security/advisories/GHSA-454m-4vm6-842f), [GHSA-wf8q-wvv8-p8jf](https://github.com/advisories/GHSA-wf8q-wvv8-p8jf), [monitoring docs](https://docs.mcphub.app/features/monitoring)).

## HomeCompute placement

The logical flow should be:

```text
Codex / Hermes owner / OpenClaw / n8n
      | private Streamable HTTP + consumer-specific key
      v
MCPHub on home-core (one endpoint; per-client group/tool scope)
      | narrowly allowed upstream connections
      +--> reviewed HTTP MCP service endpoints
      +--> selected APIs with explicitly scoped credentials
```

Keep these boundaries during the pilot:

- **No broad network bridge.** Add only exact MCPHub-to-upstream firewall
  destinations and ports. Keep the current n8n-only access to Aula and
  TilbudsTrolden until a reviewed source change admits MCPHub. Current MCP
  services intentionally have no public or wildcard publication
  ([automation runbook](../../deploy/automation/README.md)).
- **Separate client identities.** Create different revocable keys for n8n,
  Hermes owner, Codex, and OpenClaw. Give each a purpose-built group; do not
  reuse the owner key in an agent VM or put a general admin key in a client.
- **Do not onboard OpenBot's shared tool set with owner tools.** The current
  installed OpenBot interface has only a server-wide MCP list, shared among its
  agents, and no per-agent MCP grants. A gateway key or group cannot create
  isolation inside that product if all agents receive the same connection.
  Keep the existing Context7 server and defer private HomeCompute MCP access
  until per-agent enforcement or a separately isolated OpenBot instance is
  verified ([OpenBot agent setup](../openbot-agent-setup.md)).
- **Keep Home Assistant authority in its existing controller.** Do not expose
  raw write/control tools through a generic group. Start with read-only tools;
  preserve the existing proposal/review/action routes ([ADR-008](../adr/008-home-assistant-model-role.md), [ADR-029](../adr/029-proactive-assistant-context.md)).
- **Use private ingress.** Serve only over the approved LAN/tailnet paths. Keep
  the dashboard operator-only, with no public port forwarding or Funnel. Use
  HTTPS or a private TLS-terminating route and distinct keys even behind the
  existing network allow-list ([access policy](../access-policy.md)).
- **Treat MCPHub state as durable application state.** Its database contains
  users, access configuration, and credential bindings. Back up PostgreSQL
  together with the credential-encryption key; restore both as one unit before
  reopening MCP access. Do not include tool-call inputs/outputs in logs; retain
  only approved metadata and sanitized errors ([HomeCompute operations](../homecompute-operations.md), [ADR-009](../adr/009-logging-policy.md)).

MCPHub's declarative file currently manages servers and groups, not users or
client bearer keys. Those identities therefore remain in durable runtime state
unless HomeCompute adds a guarded key-provisioning lifecycle. That gap is
acceptable for a limited pilot with one operator, a small fixed key set, and a
tested backup/restore path; it must be addressed before treating the gateway as
fully reproducible infrastructure.

## Client compatibility

Codex, Hermes, and OpenClaw document remote HTTP/Streamable HTTP MCP entries
with request headers or bearer credentials ([Codex MCP](https://developers.openai.com/codex/mcp), [Hermes MCP config](https://github.com/NousResearch/hermes-agent/blob/main/website/docs/reference/mcp-config-reference.md), [OpenClaw MCP config](https://docs.openclaw.ai/gateway/config-extensions)). n8n's MCP Client Tool supports a remote endpoint with Streamable HTTP and bearer or header authentication ([n8n MCP overview](https://blog.n8n.io/mcp-vs-api/)). These are promising connection paths; test the exact pinned client versions and header support against the selected MCPHub endpoint before rollout.

OpenBot remains the exception for policy, even if its exact version can connect
over HTTP: its installed MCP list is shared across agents. Do not infer
per-agent authorization from MCPHub groups unless OpenBot supplies separate
credentials/connections per agent and the effective restrictions are verified.

## Pilot order and acceptance gates

1. Check the current LiteLLM MCP feature set/version against MCPHub; prefer the
   smaller change only if it supports the required endpoint, source-managed
   upstreams, per-client tool policy, and the same isolation requirements.
2. Build a source-managed MCPHub Compose project on `home-core`, with a pinned
   image digest, private binding, read-only settings file, dedicated database
   role/database, external credential-encryption key, resource limits, and no
   Docker socket. Use documented backup/restore for the database and key.
3. Start with synthetic/read-only MCP services and Codex as the first client.
   Test valid and invalid credentials, group/server boundary crossing, tool
   allow/deny, request-size/time limits, downstream outage, and sanitized logs.
4. Add each existing real upstream through a reviewed allow-list change and a
   service-specific smoke test. Confirm n8n's Aula route remains intentionally
   constrained; confirm no other bridge can reach it directly.
5. Add Hermes owner and OpenClaw using distinct groups and credentials. Verify
   their authenticated client paths and revocation. Keep OpenBot excluded
   unless its per-agent policy model changes and is qualified.
6. Update operational inventory, status/health checks, backup/restore, image
   update pin, and rollback documentation before considering the gateway
   generally available.

This evaluation does not establish live Home Core readiness, current deployed
MCPHub/LiteLLM versions, client compatibility, backup readiness, or firewall
behavior. Those require a separately approved implementation and read-only
live inventory followed by synthetic qualification.
