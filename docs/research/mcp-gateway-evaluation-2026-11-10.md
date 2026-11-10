# MCP gateway evaluation — 2026-11-10

## Recommendation

Use the LiteLLM Proxy MCP Gateway already deployed on Home Core. Its supported
Streamable HTTP endpoint, management UI, per-key/team/organization permissions,
and tool-level filtering match the requested control model. It also reuses the
existing proxy, Caddy front door, PostgreSQL database, secret lifecycle, and
client keys. MCPHub remains a credible choice when a separately operated MCP
registry/database is valuable; that separation is not needed for the current
Home Core inventory.

LiteLLM's official [MCP overview](https://docs.litellm.ai/docs/mcp/) documents a
fixed endpoint and permissions by key, team, or organization. Its
[permission guide](https://docs.litellm.ai/docs/mcp_control) describes
server-level grants, tool filtering, access groups, and the explicit-key-access
setting. The [client access guide](https://docs.litellm.ai/docs/proxy/access_management/mcp)
shows how to grant servers and tools to keys or teams.

## Local fit and required changes

The current source pins `ghcr.io/berriai/litellm-non_root:v1.104.2`. LiteLLM's
upstream release process was compromised for `1.107.0`; the official incident
report says the malicious package release was removed and details its recovery
actions. This proposal uses the official `1.108.4` LTS release from November 9,
not the affected tag, and pins its immutable OCI index digest
`sha256:f14a4cd90546d56a673a3a4240199da54f1912138444c3b7ab9705e01df17cdf`.
`docker buildx imagetools inspect` resolved the digest to Linux amd64 and arm64
platform manifests. The upstream publishes a cosign verification procedure;
`cosign` is unavailable locally, so this image signature remains independently
unverified. See the official [release listing](https://github.com/BerriAI/litellm/releases)
and [1.107.0 incident report](https://github.com/BerriAI/litellm/issues/47826).

The reviewed source catalog now includes these local and hosted MCP services:

| Server | Gateway target | Initial access group |
| --- | --- | --- |
| Aula | `http://aula-mcp:7878/mcp` | `aula-read` |
| TilbudsTrolden | `http://tilbudstrolden-mcp:8000/mcp` | `offers-read` |
| Context7 | `https://mcp.context7.com/mcp` | `docs-public` |
| n8n instance MCP | `http://n8n:5678/mcp-server/http` | `n8n-owner` |
| HomeCompute capability relay, Hermes principal | `https://homecompute-capabilities.home.arpa:19445/mcp` | `hermes-owner-tools` |
| HomeCompute capability relay, OpenClaw principal | `https://homecompute-capabilities.home.arpa:19445/mcp` | `openclaw-agent-tools` |
| HomeCompute capability relay, OpenBot Android principal | `https://homecompute-capabilities.home.arpa:19445/mcp` | `openbot-android-bounded-ui` |

The Android alias allowlists exactly ten tools: five read-only Portal queries
and five bounded UI interactions. It uses a dedicated `openbot-android-bounded-ui`
group, and the role's existing policy forbids message sending, form submission,
and purchases. The group is not assigned to an OpenBot key until the client
test verifies this inventory.

The source uses the Caddy hostname `mcp.home.arpa`, keeps Caddy and LiteLLM on a
private gateway bridge, puts n8n and LiteLLM on a separate internal client
bridge, and connects LiteLLM plus the two local MCP backends to a third internal
bridge. NixOS firewall rules constrain calls over the backend bridge to
LiteLLM's fixed source IP and the two MCP ports. The native capability relay's
guarded setup also permits only LiteLLM's fixed edge IP to call `/mcp`; the
relay keeps validating each existing Hermes, OpenClaw, or Android bearer. LiteLLM
trusts the relay with the read-only Home Core root certificate and sends role
tokens only through server-specific headers. Context7 is a cloud HTTPS MCP;
n8n uses a SOPS-managed bearer token mounted only into LiteLLM. The n8n group is
owner-only because its verified live catalog includes workflow execution and
administration, credential listing, agent and skill changes, and data-table
writes. Its registered names are allowlisted to the 55 tools discovered from
the authenticated Codex client on 2026-11-10. Existing direct clients stay as a
compatibility lane until their individual tool grants and workflows are migrated
and verified through the gateway.

## Access and client migration

Each server sets `allow_all_keys: false`; each is assigned to a separate access
group. LiteLLM documents that access is open if no entity has
an MCP permission list, so the rollout marks every existing unscoped virtual
key with `no-mcp-servers` before Caddy publishes the endpoint. Programmatically
created service keys also carry that sentinel. Clients receive access only
after an operator grants their separate key an access group and approved tools.
`require_key_mcp_access_defined: true` prevents team inheritance when teams do
have grants; it does not replace the explicit deny default. Prompts and
resources are disabled at the gateway because the current local services are
used for tools. The Caddy route is private and LiteLLM still requires a valid
client key.

Registering the catalog does not migrate saved n8n workflows or configure
OpenBot, Hermes, Codex, or OpenClaw. Each client should be pointed at the same
MCP endpoint and given a separate service key with only its approved access
group and tools. For the HomeCompute relay aliases, keep each client's original
role bearer in its secure client configuration and send it only in that server's
`x-mcp-<alias>-authorization` header. Review the live tool inventory before
granting access to family data. In particular, OpenBot shares its MCP list
across agents, so Aula, Hermes-owner, and n8n-owner access must not be granted
there by default.

## Security note

LiteLLM maintains a public security advisory feed. One October 27, 2026
[critical advisory](https://github.com/BerriAI/litellm/security/advisories/GHSA-w4c7-34wj-hqm8)
lists versions below `1.104.8` among affected versions, specifically for the
Enterprise proxy. Home Core uses the `litellm-non_root` image and no Enterprise
license is configured, so that advisory's Enterprise-only impact is not
asserted for this deployment. The newer LTS pin was selected to avoid carrying
an older release while adding an externally reachable MCP route. Verify the
exact deployed package edition, version, and release migrations during the
reviewed rollout.
