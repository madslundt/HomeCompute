# ADR-041: Give the owner OpenBot server scoped Nod delegation

Status: Accepted for source implementation. Live admission requires the
reviewed release, dedicated credentials, and an authenticated client smoke test.

Date: 2026-11-20

## Decision

Extend Nod's existing private background-task bridge with an OpenBot principal.
LiteLLM reaches a separate path on the native relay using its fixed source
address. The relay forwards only exact POST requests with a bounded body.
The owner OpenBot server uses a dedicated LiteLLM key and a separate Nod bridge
bearer. The `nod` alias stays outside the shared and public MCP catalogs.

Both principals use the same isolated background runtime and have independent
task ID namespaces and submission limits. Delegated work follows Nod's Trust
rules and can use the instance's connected apps and browser sign-in, with owner
intervention handled in Nod. The bridge returns only the matching session's
status and bounded text result. OpenBot never receives Nod's owner login token.

## Consequences

- OpenBot can ask the running Nod assistant through its existing private
  gateway, without changing the agents VM's outbound rules.
- Every agent on the selected OpenBot server can use the connection. Provision
  it on the owner server. The household and maintenance servers keep their
  existing scopes.
- Nod holds run state and results. The OpenBot and Hermes delegates cannot
  look up each other's requests, even if their request UUIDs match.
- The bridge continues to depend on the reviewed Nod source pin. Requalify
  the patched runtime and delegated app behavior when upgrading Nod.

See [Nod activation and rollback](../../deploy/nod/README.md#openbot-delegation)
and [OpenBot enrollment](../../deploy/openbot/README.md#ask-the-local-nod-assistant).
