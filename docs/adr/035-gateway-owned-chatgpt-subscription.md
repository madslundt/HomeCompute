# ADR-035: Explicit ChatGPT subscription routes on LiteLLM

Status: Accepted; rollout requires the normal reviewed release and an
interactive account sign-in.

Date: 2026-11-12

## Context

Home Core's LiteLLM gateway currently routes its ordinary capabilities to local
inference. LiteLLM 1.109 supports routing requests to ChatGPT's subscription
backend, but that mode uses a gateway-owned OAuth login rather than forwarding a
different ChatGPT account from each client. The account credential is a
refreshable file that LiteLLM must be able to update in place.

The owner requested three explicit ChatGPT family aliases after the generic
`gpt-*` and `codex-*` routes proved unsuitable. A single gateway identity makes
those routes available to any virtual key granted access, so the names and key
scopes must make the cloud selection visible and intentional.

## Decision

Expose exactly these aliases in LiteLLM's model list:

| LiteLLM alias | Current ChatGPT provider target |
| --- | --- |
| `chatgpt-sol-latest` | `chatgpt/gpt-6.1-sol` |
| `chatgpt-terra-latest` | `chatgpt/gpt-6.1-terra` |
| `chatgpt-luna-latest` | `chatgpt/gpt-6-luna` |

The `-latest` suffix is a stable HomeCompute alias, not a provider-managed
automatic update. Change its upstream target through a reviewed model-registry
change when a new supported model is selected. Do not reintroduce wildcard
`gpt-*` or `codex-*` deployments.

Use one ChatGPT subscription identity owned by the LiteLLM gateway. Provision
its OAuth device flow into `/srv/state/control-plane/chatgpt-auth/auth.json`,
outside Git, and mount only that directory read/write into the non-root LiteLLM
container at `/run/homecompute/chatgpt`. The directory is mode 0700 and owned by
UID/GID 101. The auth file remains writable because refresh tokens rotate in
place. Provision it interactively after the source release installs the path;
do not place the credential in the generic Admin UI provider credential form,
repository files, shell history, or chat.

Virtual keys receive only the model aliases they need. Keep the existing
keyless network-inference scope local-only, keep MCP grants separate, and do not
configure an automatic API-key fallback. A key with one of these aliases uses
the shared gateway ChatGPT account; LiteLLM does not provide per-key ChatGPT
identities in this mode. Consumers must select an alias explicitly before
sending request content to ChatGPT. Existing private workflows remain on their
local aliases and do not gain cloud fallback.

The aliases carry zero per-token API cost in LiteLLM because the subscription is
not billed by API token. Control access with model scopes and request/token
limits; a dollar budget does not cap subscription quota.

## Consequences

- LiteLLM now stores a durable, refreshable ChatGPT account credential on Home
  Core. The gateway, host administrator, and backups can access it; protect the
  directory and include it in the control-plane credential restore procedure.
- Every key allowed to call the aliases consumes the same account and plan
  quota. Revoking the account session or removing the auth file stops all three
  aliases until an operator signs in again.
- The model choices remain visible and independent of local inference. An
  upstream quota or account restriction returns an error; it does not reroute
  a local workflow to cloud.
- The LiteLLM `Add Credential` form is not the login workflow. The documented
  device flow writes the provider's refreshable auth file to the mounted path.

## Alternatives considered

### Client-owned subscription login

Codex client BYOK keeps each person's login on their own device. This is not
the requested gateway-owned credential path and does not provide the requested
named aliases for other LiteLLM clients.

### Generic `gpt-*` and `codex-*` routes

Rejected because their broad patterns were not useful in the Admin UI workflow
and obscure which model a virtual key should be allowed to call.

### OpenAI API key or automatic fallback

Rejected because API billing is a separate credential and account. No paid API
fallback is authorized by this decision.
