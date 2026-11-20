# ADR-042: Use the owner OpenBot connection for Nod

Status: Accepted.

Date: 2026-11-20

## Decision

Add the `openbot-nod` group to the existing `openbot-agent` LiteLLM key.
The owner OpenBot server uses its existing `homecompute` MCP connection.
It sends Nod's dedicated bearer in `x-mcp-nod-authorization`, which LiteLLM
forwards only to the Nod alias.

This supersedes the separate OpenBot connection in
[ADR-041](041-openbot-nod-delegation.md). Nod continues to authenticate the
OpenBot delegate independently of Hermes. The gateway keeps the Nod alias
outside the general harness grant and the LAN catalog. Every agent using
the owner server's connection can delegate work to Nod.

See the [OpenBot runbook](../../deploy/openbot/README.md#ask-the-local-nod-assistant)
for verification and rollback.
