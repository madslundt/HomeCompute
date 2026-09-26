# Hermes guest runtime

This directory documents the guest-side deployment boundary for the first,
synthetic Hermes canary. The NixOS configuration creates and isolates the
Ubuntu `agents` guest; the helper in this repository runs **inside that guest**
as its unprivileged operator.

The pilot is intentionally narrow:

- NemoClaw `v0.0.129` at commit
  `26922313bba96184e65c3663b351683ebae9504d`;
- the release-managed Hermes `0.21.3` and OpenShell `0.0.116` tuple;
- one sandbox named `agent-owner` using `assistant-canary` at
  `http://ai.home.arpa:18080/v1` over the isolated agents bridge. The host
  verifies and forwards the upstream TLS connection to `ai.home.arpa`; this
  compatibility hop exists because the pinned OpenShell 0.0.116 inference
  client does not consume NemoClaw's imported private CA;
- Restricted policy, no web search, no messaging, no MCP servers, no host
  mounts, and synthetic data only.

The machine-readable tuple is in
[`config/hermes-release.json`](../../config/hermes-release.json). Do not update
Hermes or OpenShell independently of NemoClaw.

## Prepare the guest operator

Install the documented NemoClaw prerequisites in the Ubuntu 24.04 guest:
Docker Engine, Git, curl, jq, OpenSSL, Python 3, Node.js 22.19 or newer, and npm
10 or newer. The final guest operator must be able to use Docker without sudo.
Docker group membership is root-equivalent inside the guest and must not be
given to household accounts.

Create operator-owned directories with private modes. The paths below match the
example config; a secret manager may materialize the key at the same path.

```bash
sudo install -d -m 0750 -o "$USER" -g "$USER" /etc/homecompute-hermes
sudo install -d -m 0700 -o "$USER" -g "$USER" /etc/homecompute-hermes/secrets
sudo install -d -m 0700 -o "$USER" -g "$USER" /var/lib/homecompute-hermes/evidence
sudo install -d -m 0700 -o "$USER" -g "$USER" /var/lib/homecompute-hermes/gates
install -m 0600 config/hermes-agent.env.example /etc/homecompute-hermes/agent-owner.env
sudo install -m 0600 -o "$USER" -g "$USER" \
  artifacts/home-core-root.crt /etc/homecompute-hermes/home-core-root.crt
```

Write the dedicated, alias-scoped LiteLLM virtual key without placing it in a
command argument or shell history. The file must contain only the key and must
be mode `0600`:

```bash
umask 077
read -r -s -p 'agent-owner LiteLLM key: ' HERMES_KEY
printf '\n'
printf '%s\n' "$HERMES_KEY" > /etc/homecompute-hermes/secrets/litellm-agent-owner
HERMES_KEY=
unset HERMES_KEY
```

The key must be authorized only for `assistant-canary`, with its own budget and
rate limit. Never use the LiteLLM master key or the automation client key.

The mutating commands also require two external readiness records at the paths
in the config: `backup-readiness.json` and `agents-network.json`. They are not
created by this helper. The durable backup gate retains this mode-`0600`
schema, while the network record uses the corresponding `agents-network` gate:

```json
{
  "schema_version": 1,
  "gate": "off-host-backup",
  "status": "ready",
  "scope": "hermes-synthetic-canary",
  "observed_at": "2026-09-26T12:00:00Z",
  "evidence": "operator reference to the reviewed restore evidence"
}
```

For the synthetic bootstrap only, the backup path may instead hold this exact,
temporary exception schema:

```json
{
  "schema_version": 1,
  "gate": "local-bootstrap-backup",
  "status": "ready",
  "scope": "hermes-synthetic-canary",
  "observed_at": "2026-09-26T12:00:00Z",
  "durability": "same-host-same-disk",
  "risk_acknowledged": true,
  "snapshot_id": "agents-vm-bootstrap-20260926",
  "restore_evidence": "reference to the completed synthetic restore drill",
  "limitations": "Loss of the home-core disk loses source and backup.",
  "data_classification": "synthetic-only",
  "allowed_sandbox": "agent-owner",
  "allowed_model": "assistant-canary",
  "integrations": "none",
  "expires_at": "2026-09-27T12:00:00Z"
}
```

Every field is mandatory and additional fields are rejected. `expires_at` must
still be in the future whenever a mutating command runs. This exception admits
only the already hard-coded `agent-owner`/`assistant-canary` sandbox with
synthetic data and no messaging, web search, MCP, host mounts, or real personal
data. It does not satisfy the production household rollout gate. Replace it
with the durable `off-host-backup` record before creating any other user,
adding an integration, or importing real data.

`install`, `onboard-canary`, `snapshot`, and `restore-verify` fail before
invoking NemoClaw when either record is absent, invalid, or—when using the
local exception—expired. `validate` and `preflight` remain available while the
deployment is still blocked.

## Validate, install, and onboard

Run each gate separately from the reviewed HomeCompute checkout inside the
guest:

```bash
config=/etc/homecompute-hermes/agent-owner.env
./scripts/setup-hermes-guest.sh validate --config "$config"
./scripts/setup-hermes-guest.sh preflight --config "$config"
./scripts/setup-hermes-guest.sh install --config "$config"
./scripts/setup-hermes-guest.sh onboard-canary --config "$config"
./scripts/setup-hermes-guest.sh health --config "$config"
```

`install` downloads the root installer from the pinned 40-character commit,
verifies its reviewed SHA-256 digest, and performs deferred onboarding. It does
not pipe an unverified mutable URL to a shell. `onboard-canary` reads the key
from its private file and passes it only in the child process environment that
registers it with OpenShell. It explicitly clears common messaging and search
credential variables inherited from the operator shell.

The health command writes mode-`0600` evidence beneath the configured evidence
directory. It checks the host CLI version, global doctor result, sandbox Ready
state, in-sandbox inference health, selected model, OpenShell version, Hermes
version, and `connect --probe-only`. The output is designed to be redacted by
NemoClaw, but still treat it as operationally sensitive.

## Snapshot and restore drill

Create a named snapshot only after the synthetic canary is healthy:

```bash
./scripts/setup-hermes-guest.sh snapshot synthetic-baseline \
  --config /etc/homecompute-hermes/agent-owner.env
```

Verify it by restoring into the fixed, non-production name
`agent-owner-verify`:

```bash
./scripts/setup-hermes-guest.sh restore-verify synthetic-baseline \
  --config /etc/homecompute-hermes/agent-owner.env
```

The helper refuses to overwrite an existing verification target and never uses
`--force` or destroys a sandbox. It intentionally leaves the restored sandbox
for inspection. Remove it later with the reviewed NemoClaw lifecycle command
only after checking its evidence and confirming that no diagnostic process
still depends on it.

NemoClaw snapshots remain under the guest's NemoClaw state and are not an
off-host backup by themselves. Provider credentials are not snapshot payload;
restore procedures must re-register them from the external secret source when
the OpenShell provider store is also lost.

## Production stop conditions

Do not put personal data or messaging credentials into this canary. Stop before
household expansion if any of these are true:

- the host-only guest network or `assistant-canary` route is not qualified;
- encrypted off-host backup and a clean restore drill have not passed;
- the observed versions differ from the recorded tuple;
- the raw LiteLLM credential is visible inside the sandbox or evidence;
- OpenShell cannot keep credentials and policies sandbox-specific;
- reboot recovery, outage behavior, or cross-sandbox denial tests fail.

Adding the partner, children, family messaging, or real memories is a later
serial rollout after the synthetic acceptance gates pass.
