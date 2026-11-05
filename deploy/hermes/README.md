# Hermes household owner agent

Hermes is being reintroduced alongside OpenClaw in the isolated Ubuntu agents
VM. The owner uses the dedicated Mattermost bot `hermes-agent`; the existing
n8n `hermes` identity is independent. Additional profiles are deferred.

## Release and policy

`config/hermes-release.json` pins NemoClaw 0.0.141, OpenShell 0.0.116, and
NVIDIA's complete managed Hermes sandbox by digest. This integrates Hermes
0.21.3. Standalone Hermes 0.22.7 is newer, but is not yet qualified with the
managed startup, credential, recovery and browser contracts. Do not replace
the managed image with the standalone upstream image: it omits these contracts.

Use local `automation-moe` through the private compatibility bridge. Hermes
has its own LiteLLM virtual key, restricted to that alias. No cloud fallback is
configured. Mattermost access is limited to the owner user ID in the non-secret
config. Tokens remain in private guest secret files and OpenShell's credential
store. Keep personal data out of operational evidence and Git.

## Prerequisites and setup

Read `docs/homecompute-operations.md` and the agents VM runbook before live work.
Use Ubuntu 24.04, Docker, Node 22.19+, npm 10+, Python 3, curl, jq, and OpenSSL.
The operator needs Docker access. Use the Node path selected by the managed
HomeCompute wrapper when running unattended SSH commands.

Copy `config/hermes-agent.env.example` into an operator-owned private directory,
mode 0600. Provision the LiteLLM key with
`scripts/provision-hermes-litellm-key.py` once, and store the dedicated bot token
at the configured secret path, mode 0600. Never put tokens into argv or Git.

Every mutation requires external mode-0600 readiness records for
`off-host-backup` and `agents-network`, each with schema_version 1, status
`ready`, scope `hermes-household`, observed_at in UTC, and an evidence reference.
The backup record must reference a fresh encrypted Synology restore including
archive integrity checks, PostgreSQL imports and isolated agents VM boot proof.
A same-host bootstrap backup does not admit household messages.

From the reviewed, published source checkout inside the guest:

```bash
config=/home/hermes-operator/.config/homecompute-hermes/hermes-owner.env
./scripts/setup-hermes-guest.sh validate --config "$config"
./scripts/setup-hermes-guest.sh preflight --config "$config"
./scripts/setup-hermes-guest.sh onboard-canary --config "$config"
./scripts/setup-hermes-guest.sh health --config "$config"
./scripts/setup-hermes-guest.sh connect-mattermost --config "$config"
```

Onboarding uses the exact published managed image, Restricted policy, no host
mounts, and no search-provider credentials. Browser capability requires its own
live public-page and private-destination-denial checks before readiness is
claimed. The Mattermost policy allows only its restricted private TLS proxy.

The Homepage card opens the dedicated Mattermost DM. Dashboard forwarding is
not required for that path; keep the dashboard private until its authentication
and ingress have been separately verified.

## Verification and recovery

Require Ready status, an authenticated local inference request, a Mattermost
owner DM and reply, public browsing, denied private browsing, and supported
stop/start recovery. Inspect memory and disk use under load. Synthetic probes
alone do not prove household readiness. `health` retains private evidence under
the configured evidence directory.

Use `snapshot NAME` only after readiness, and `restore-verify SNAPSHOT` only as
an intentional state replacement. Retain the previous release and backup;
code rollback does not undo data changes.

Failed custom-image attempts named `agent-owner`, `hermes`, and `hermes-main`
left immutable recovery records. Native destroy currently refuses conflicting
authority. Preserve those records and exact container IDs. Never remove their
tracking or destroy resources by a mutable name to bypass the refusal. Use a
new uniquely named sandbox only after independently qualifying corrected image
inputs. Oh My Hermes is deferred until the core service is stable.
