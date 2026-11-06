# Hermes household owner agent

Hermes is being reintroduced alongside OpenClaw in the isolated Ubuntu agents
VM. The owner uses the dedicated Mattermost bot `hermes-agent`; the existing
n8n `hermes` identity is independent. Additional profiles are deferred.
See [the dated runtime evidence](../../docs/hermes-runtime-validation-2026-11-06.md)
for applied revisions, measured checks and remaining qualification gaps.

## Release and policy

`config/hermes-release.json` pins NemoClaw 0.0.141, OpenShell 0.0.116, and
NVIDIA's complete managed Hermes sandbox by digest. This integrates Hermes
0.21.3 as its base. The selected [managed overlay](latest/README.md) upgrades
Hermes itself to 0.23.0 while retaining the startup, credential and recovery contracts. Do not replace
the managed image with the standalone upstream image: it omits these contracts.

Use local `automation-moe` through the private compatibility bridge. Hermes
is capped at 65,536 context tokens, below the observed backend capacity, to
keep its memory and compaction behavior predictable. Hermes
uses the backend-supported `medium` reasoning setting for interactive work;
`high` is rejected by this model's current server. Hermes
has its own LiteLLM virtual key, restricted to that alias. No cloud fallback is
configured. Mattermost access is limited to the owner user ID in the non-secret
config. Tokens remain in private guest secret files and OpenShell's credential
store. Keep personal data out of operational evidence and Git.
Inference settings retain the managed `${COMPATIBLE_API_KEY}` environment
expression. Never persist a resolved `openshell:resolve:env:...` credential
reference: its runtime scope can change across a native restart or VM boot.

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
config=/home/hermes-operator/.config/homecompute-hermes/hermes-personal-v23.env
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
It requires an explicit authenticated synthetic inference response even when
the vendor CLI intentionally suppresses inference probes in ordinary status.

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

The image-owned loopback adapter handles CDP clients that bypass HTTP proxy
settings. It forwards only the fixed browser WebSocket path through OpenShell,
using the opaque credential in the upgrade request. Hermes 0.23's `browser_exec` and the Python dialog supervisor use that adapter;
it exposes no general proxy route. The old `browser.backend` selector is removed.
The release-pinned browser-harness package supplies the browser tool without an
external browser service or a runtime package download.

`benchmarks/plans/hermes-local-runtime-smoke.json` exercises three 16-call
synthetic sequences with retry recovery and strict final JSON. Supply a private
release manifest with the observed runtime provenance and the endpoint/key
environment variables named in the plan. This operational screen is separate
from ADR 025's artifact promotion, long-context and overnight soak gates.
The older A/B fixture does not prescribe its asserted call order and can return
positional records for a different legal order; retain its results as diagnostic,
not as passing model qualification.

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

The installer uses the guest operator's existing noninteractive sudo to install
a tracked system drop-in ordering `user@1000.service` after Docker. This ensures
Hermes can record a native stop before Docker terminates containers at shutdown.
The user unit also follows `default.target`, matching the vendor gateway's
ordering. Verification includes the complete user boot transaction and rejects
cycle warnings even if systemd returns exit status zero.

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

The selected runtime name is `hermes-personal-v23`. The previous
`hermes-personal` instance and its configuration are retained for rollback. An earlier `hermes-owner`
replacement remains retained and stopped after NemoClaw 0.0.141 failed to merge
its default nonempty kanban directory during native workspace restoration.
Its recovery copy and immutable tracking were preserved. The owner authorized
a clean start, so the final instance uses no native state import. The supported
household recovery boundary remains the complete encrypted agents VM archive.
The final bootstrap also used the approved clean-start path after the first
full shutdown left a terminal Error record. That VM archive was retained, the
shutdown ordering was repaired in source, and a later full VM reboot passed.
The temporary no-backup recreation option is not a routine household upgrade
or recovery policy.

## Upgrade cutover and rollback

The source-owned `setup-hermes-owner-config.py` installs the canonical non-secret
`config/hermes-agent.env.example` into the private new owner config, exclusively
and idempotently. It refuses an unknown existing file and preserves the previous
config and all durable state. The new instance starts with clean Hermes state;
no native workspace import is attempted.

Qualify the new instance with its Mattermost listener disabled. Stop
`homecompute-hermes-personal.service` **before** installing its replacement unit:
the installed old `ExecStop` must still address the previous instance. Require
native `hermes-personal` status `Stopped`; the Mattermost connector enforces this
before enabling the new listener. Existing gateway-held provider IDs remain
`hermes-personal-mattermost` and `hermes-personal-browser`, so no new bot or token
is needed. Install startup for the new config only after its client checks pass.
Qualification commands refuse to reconfigure the selected owner instance.

For rollback, stop the new unit first, natively stop `hermes-personal-v23`, and
install/start the retained previous source's unit with `hermes-personal.env`.
Health-check that release and the owner DM/browser path. Leave the new instance
stopped with its state preserved; switching code does not merge conversation
histories. The retained old application state is the rollback state, while the
encrypted whole-VM Synology archive is the disaster-recovery boundary.
