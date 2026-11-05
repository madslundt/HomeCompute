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
config=/home/hermes-operator/.config/homecompute-hermes/hermes-personal.env
./scripts/setup-hermes-guest.sh validate --config "$config"
./scripts/setup-hermes-guest.sh preflight --config "$config"
./scripts/setup-hermes-guest.sh onboard-canary --config "$config"
./scripts/setup-hermes-guest.sh health --config "$config"
./scripts/setup-hermes-guest.sh connect-mattermost --config "$config"
```

Onboarding builds a thin overlay on the exact published managed image. The
overlay pins the local model and endpoint in the entrypoint environment, since
the publisher defaults otherwise override native gateway routing after restart.
It uses Restricted policy, no host
mounts, and no search-provider credentials. Browser capability requires its own
live public-page and private-destination-denial checks before readiness is
claimed. The Mattermost policy allows only its restricted private TLS proxy.

The Homepage card opens the dedicated Mattermost DM. Use `scripts/setup-hermes-homepage.py --revision FULL_SHA` on home-core to
inspect the card patch, then apply with the reported `--expected-sha256` and
`--apply`. This guarded lifecycle changes only the card and restarts Homepage.
Dashboard forwarding is
not required for that path; keep the dashboard private until its authentication
and ingress have been separately verified.

## Verification and recovery

Require Ready status, an authenticated local inference request, a Mattermost
owner DM and reply, public browsing, denied private browsing, and supported
stop/start recovery. Inspect memory and disk use under load. Synthetic probes
alone do not prove household readiness. `health` retains private evidence under
the configured evidence directory.

NemoClaw 0.0.141 has no per-sandbox `snapshot` command. Recovery uses the
owner-approved encrypted Synology application archive, including the whole
agents VM, with the repository's isolated restore verification. Retain the
prior release and archive; code rollback does not undo data changes. The old
`HERMES_RESTORE_TARGET` config field remains accepted for compatibility but
exposes no restore action.

Failed custom-image attempts named `agent-owner`, `hermes`, and `hermes-main`
left immutable recovery records. Native destroy currently refuses conflicting
authority. Preserve those records and exact container IDs. Never remove their
tracking or destroy resources by a mutable name to bypass the refusal. Use a
new uniquely named sandbox only after independently qualifying corrected image
inputs. Oh My Hermes is deferred until the core service is stable.

## Dedicated browser worker

The browser uses a separate profile volume, token, Docker networks
`172.30.190.0/24` and `172.30.191.0/24`, and bridge listener
`172.18.0.1:18810`. OpenClaw retains its existing `.188/.189` worker. The
Chromium container is read-only, unprivileged, and has no household state or
inference credentials. Its proxy denies private, loopback, link-local and
reserved destinations. The gateway holds the browser token; Hermes stores
a credential-free `BROWSER_CDP_URL`. The image-owned client wrapper appends the
opaque reference only when connecting to the exact owned endpoint.

Stage the published `deploy/openclaw/tools` and browser installer in an
operator-owned guest release directory. Use the qualified image ID from
`config/hermes-release.json`, with its existing local artifact or reviewed
recovery archive, rather than pulling an unpinned image:

```bash
python3 scripts/setup-openclaw-browser.py --principal hermes \
  --directory "$release/deploy/openclaw" --image-id "$browser_image_id"
./scripts/setup-hermes-guest.sh connect-browser --config "$config"
```

The installer refuses existing browser containers. Inspect their identity and
retained profile before any replacement. Keep the generated `private` directory
and browser profile volume outside Git and include them in the guest backup.
The worker's stable `/devtools/browser/homecompute` endpoint resolves the local
Chromium ID on each connection, so a browser restart does not stale the URL.

During this clean installation, failed synthetic requests produced diagnostic
request dumps rejected by NemoClaw's state-archive credential scan. The owner
explicitly authorized discarding Hermes state. The supported one-time
`NEMOCLAW_RECREATE_WITHOUT_BACKUP=1` recreation option was used; this is not a
standing backup exception. Household state replacement still requires a verified
off-host archive and explicit data-loss intent.

## Boot lifecycle

After authenticated messaging and browser checks pass, run
`setup-hermes-guest.sh install-startup --config "$config"` from the selected
published guest release. It renders the tracked user unit, verifies it with
systemd, enables it, and exercises its stop/start path. The vendor-managed
`nemoclaw-openshell-gateway.service` must already be enabled; user lingering
must be enabled so the user manager starts at VM boot.

This is a boot/shutdown unit calling NemoClaw's native lifecycle. It does not
respawn the in-sandbox gateway or compete with Hermes's process supervision.
The sandbox's Docker restart policy remains owned by OpenShell. Keep the
source release referenced by the unit. Update it through the same installer
when selecting a newer qualified release.

The image disables upstream error-path request dumps at the narrow dump hook.
Normal metadata diagnostics remain enabled; full prompts, responses and tool
payloads belong only to Hermes's intentional conversation state, not duplicate
request-debug files. The build rejects an unexpected upstream implementation
instead of silently skipping this privacy policy.

The final runtime name is `hermes-personal`. An earlier `hermes-owner`
replacement remains retained and stopped after NemoClaw 0.0.141 failed to merge
its default nonempty kanban directory during native workspace restoration.
Its recovery copy and immutable tracking were preserved. The owner authorized
a clean start, so the final instance uses no native state import. The supported
household recovery boundary remains the complete encrypted agents VM archive.
