# MCP access inventory — 2026-11-10

This inventory records MCP endpoints supported by tracked HomeCompute source
and the latest operational evidence found in the repository. It distinguishes
MCP servers from services exposed through ordinary HTTP/API tools. A source
declaration or dated observation does not prove current live state.

## Servers and clients with evidence

| MCP server or endpoint | Location / transport | Client(s) and evidence | Status and gateway implications |
| --- | --- | --- | --- |
| Aula MCP | Home Core; `http://aula-mcp:7878/mcp` on the automation network (Streamable HTTP); host loopback `http://127.0.0.1:17878/mcp`; legacy SSE `/sse`; no upstream client auth | n8n production service is declared in [`deploy/automation/production.yaml`](../../deploy/automation/production.yaml) and documented in [`deploy/automation/README.md`](../../deploy/automation/README.md#aula-mcp) (see section “Aula MCP”). Hermes’s actual client calls were verified 2026-11-06 in [`docs/hermes-mcp-validation-2026-11-06.md`](../hermes-mcp-validation-2026-11-06.md). Current LiteLLM catalog declares this backend in [`deploy/control-plane/litellm-config.yaml`](../../deploy/control-plane/litellm-config.yaml). | Source-backed Home Core server. The runbook describes firewall-limited n8n access and no LAN/Tailscale/reverse-proxy publication. Hermes’s verification also proves Aula access through the separate HomeCompute capability bridge. Confirm live container, n8n saved clients, and current LiteLLM backend health before claiming synchronized access. |
| TilbudsTrolden MCP | Home Core; `http://tilbudstrolden-mcp:8000/mcp` on the automation network (Streamable HTTP); host loopback `http://127.0.0.1:17879/mcp`; no upstream client auth | n8n production service is declared in [`deploy/automation/production.yaml`](../../deploy/automation/production.yaml) and documented in [`deploy/automation/README.md`](../../deploy/automation/README.md#tilbudstrolden-mcp). Hermes’s actual shopping-store call was verified 2026-11-06 in [`docs/hermes-mcp-validation-2026-11-06.md`](../hermes-mcp-validation-2026-11-06.md). Current LiteLLM catalog declares this backend in [`deploy/control-plane/litellm-config.yaml`](../../deploy/control-plane/litellm-config.yaml). | Source-backed Home Core server. Docs describe n8n-only routed access, loopback host publication, and outbound HTTPS for offer data. Confirm live container, n8n clients, and current LiteLLM backend health. |
| Context7 hosted MCP | `https://mcp.context7.com/mcp`; remote Streamable HTTP MCP | The current Codex desktop config has an enabled `context7` entry. [`scripts/openclaw_integrations.py`](../../scripts/openclaw_integrations.py) defines the fixed endpoint and two public-documentation tools; [`config/openclaw-integrations.json`](../../config/openclaw-integrations.json) enables that adapter. [`docs/openbot-agent-setup.md`](../openbot-agent-setup.md) reports OpenBot's shared server MCP list contained only `context7` on 2026-11-09. | Added to the LiteLLM catalog under `docs-public`, with `allow_all_keys: false`. Current OpenBot/OpenClaw client state still needs migration and verification. |
| n8n instance MCP | Home Core; `http://n8n:5678/mcp-server/http` on the internal `mcp-clients` network | The current Codex desktop config has an enabled n8n entry. An authenticated live Streamable HTTP `tools/list` on 2026-11-10 returned 55 tools. The catalog includes workflow execution/publication/editing, credential listing, agent/skill administration, and data-table writes. | Added under `n8n-owner`; its bearer comes from the SOPS-managed `control-plane/n8n-mcp-token`. The catalog allowlists the 55 observed names, but gateway clients get no access by default. The group is high authority. Existing direct clients remain until migrated individually. |
| HomeCompute capability bridge | Home Core agents bridge; `https://homecompute-capabilities.home.arpa:19445/mcp`, Streamable HTTP, bound to `10.77.20.1`; separate principals | Hermes actual MCP discovery and seven read-only calls passed before/after restart on 2026-11-06; 50 tools were discovered. OpenClaw's NemoClaw route and OpenBot's staged Android entry also reference it. | LiteLLM declares separate `homecompute_hermes`, `homecompute_openclaw`, and `homecompute_openbot_android` aliases with distinct access groups and OAuth-delegated upstream bearer headers. Its guarded relay lifecycle must be applied to admit only LiteLLM's fixed address; the relay still validates each role token. Client migration remains pending. |
| OpenBot Android MCP principal | Same Home Core capability bridge endpoint above; separate `openbot-android` bearer; five read tools and five bounded UI interaction tools | [`docs/openbot-android-mcp.md`](../openbot-android-mcp.md) defines the intended server entry and guarded host lifecycle. But [`docs/openbot-agent-setup.md`](../openbot-agent-setup.md) says OpenBot’s live shared MCP list contained only `context7`; it also says that list is shared across server agents. | The LiteLLM alias allowlists exactly these ten tools under `openbot-android-bounded-ui`. It is not established as a current OpenBot registration. The Android integration instructions require a connection test and exact ten-tool inventory before saving; current OpenBot egress policy may not reach the gateway. |

## Not established as registered MCP servers

- **OpenClaw managed task MCP:** The proposed `https://openclaw-broker.home.arpa/mcp`
  route and Caddy file are explicitly staged, not imported/deployed; its docs
  say no credential or managed registration was attempted. See
  [`docs/openclaw-operations.md`](../openclaw-operations.md) and
  [`deploy/openclaw/nemoclaw/mcp.Caddyfile`](../../deploy/openclaw/nemoclaw/mcp.Caddyfile).
- **Codex local stdio servers:** The desktop config also has enabled `cua_repl`,
  `node_repl`, `playwright`, and `solidcraft` process servers. The first two
  bind to the ChatGPT desktop runtime, Playwright starts a local browser process,
  and SolidCraft launches its desktop CLI. These are workstation/session
  resources, not portable Home Core HTTP backends. Codex's app-tools and
  computer-use process entries are disabled. Do not launch them on Home Core or
  advertise them through the shared gateway.
- **Codex worker broker:** Repository code defines a separate bounded worker
  MCP adapter, but it is not configured in the user's desktop/CLI client and
  its external route remains staged.
- **Other cloud MCPs:** No additional active cloud MCP URL/config was found in
  tracked runtime source. Context7 is the only concrete cloud MCP endpoint found.
  The October 28 research note lists Google Maps Grounding Lite and Expedia as
  candidates and explicitly treats them as conditional/research, not active
  integrations; neither should be registered from that evidence.
- **Home Assistant legacy Aula MCP:** The September 4 migration inventory records
  an older HAOS endpoint `http://homeassistant.local:7878/sse`. It predates the
  Home Core Aula deployment and is historical/stale evidence, not a current
  server to add without rechecking HAOS and the saved workflows.
- **OpenClaw domain integrations:** Context7, Geoapify, SerpApi, Immich, n8n and
  Open-Meteo appear in the role-scoped adapter. Apart from Context7, these are
  fixed REST/API integrations, not MCP upstream servers. For example, the
  Context7/places/hotels/photo/workflow tools are declared by
  [`scripts/openclaw_integrations.py`](../../scripts/openclaw_integrations.py);
  do not treat those APIs as extra MCPs. The HomeCompute facade itself may be
  registered with an agent without turning each API into an MCP backend.
- **Codex app connectors:** GitHub, Notion, Calendar and other Codex app
  connectors appear as desktop tools, not MCP server URLs in the user's Codex
  MCP configuration. They cannot be imported into LiteLLM as backend MCP
  servers without a separate provider MCP registration and credentials.

## Current source-defined LiteLLM scope

The LiteLLM source catalog contains Aula, TilbudsTrolden, Context7, n8n, and
three per-principal aliases for the HomeCompute capability relay, each assigned
to a named access group, with `allow_all_keys: false`. The gateway evaluation
records that existing n8n clients remain on direct backend paths until migrated;
the gateway is therefore not yet the sole ingress. See
[`docs/research/mcp-gateway-evaluation-2026-11-10.md`](mcp-gateway-evaluation-2026-11-10.md),
[`docs/adr/036-share-lite-llm-mcp-catalog-across-harnesses.md`](../adr/036-share-lite-llm-mcp-catalog-across-harnesses.md), and
[`deploy/control-plane/litellm-config.yaml`](../../deploy/control-plane/litellm-config.yaml).
The owner has selected the shared `all-harness-mcps` group for every harness
key, while model-only service keys remain denied. The source change alone does
not migrate saved OpenBot, Hermes, OpenClaw, Codex, or n8n client state. The
HomeCompute relay aliases preserve their distinct principal credentials, but
their fixed-source ingress update still needs the guarded lifecycle and the
client settings still need migration.

## Live checks still needed

Before declaring the complete available set or migrating clients, inspect and
record sanitized results for:

1. `home-core`: current containers/listeners for Aula, TilbudsTrolden, n8n,
   LiteLLM, and the capability bridge; current LiteLLM server catalog, tool
   inventories, client keys/groups, and direct n8n network access.
2. n8n: active saved workflow MCP URLs/transports, including whether any legacy
   HAOS endpoint remains; determine which workflows actually use the direct
   Aula/TilbudsTrolden paths.
3. OpenBot: current server MCP list and tool inventory. Its shared server-level
   settings affect every agent. Verify all seven aliases after assigning
   `all-harness-mcps`; verify the Android alias still exposes only its ten
   reviewed tools and that each delegated relay has the intended role bearer.
4. Hermes: current native MCP list/policy and discovered tools. The November 6
   evidence verifies the HomeCompute bridge at that time but does not enumerate
   unrelated user-configured cloud servers.
5. OpenClaw: actual native/NemoClaw registration and provider readiness. The
   current plain source config has an empty native server map; role tools may
   instead arrive through the managed capability bridge and API facade.
6. Codex desktop/CLI and other personal clients: inspect their real user-level
   MCP settings, which are outside this repository. Also inspect any other
   local/cloud MCP catalogs those clients can access; Git search cannot enumerate
   account-level marketplace or UI settings.

No secrets, bearer values, workflow payloads, or private account data are
included here. This is a source/evidence inventory, not proof that all services
are currently healthy or that every client is synchronized.
