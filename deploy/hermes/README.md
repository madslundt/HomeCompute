# Hermes household owner agent

## Hands-free browser voice

The owner URL `https://agents.home.arpa/hermes/` serves Hermes's native Webapp
with its browser microphone and continuous voice conversation controls. The
older administrative dashboard embeds a terminal and does not have those
browser controls. Its loopback listener remains available for operator recovery.
Both surfaces retain native password authentication; the Webapp uses the same
owner account. No public listener or external speech provider is introduced.

After signing in, open a new conversation, choose **Start voice conversation**
from the microphone menu, and allow microphone access. Hermes listens again
after replying. Say **stop**, **stop samtalen**, or **goodbye hermes** to end the
voice session. Use headphones for the most reliable interruption and echo
handling. Browser microphone permissions, certificate trust and device audio
remain client prerequisites.

`config/hermes-speech.json` owns the Whisper/OmniVoice integration and the
offline dependency/artifact pins. Whisper detects the input language. A seeded,
offline Danish/English classifier selects `tts-danish`/`danish-default` or
`tts-english`/`english-default` from each reply sentence; short ambiguous text
can select the wrong language. The command provider's `voice` setting can pin
either approved voice instead of `auto`. It uses complete WAV sentences, not a
provider-native realtime speech model. The sandbox receives only its existing
opaque managed owner credential, admitted to the two exact audio POST paths.
The retired `hermes` bearer remains denied. The trusted adapter keeps its
existing input limits, speech verification and local fallback behavior.

Build the Webapp from the release-pinned source in a disposable container of
the qualified Hermes image:

```bash
python3 scripts/build-hermes-webapp.py --output /absolute/private/new-build
```

The builder uses the pinned Node/npm and upstream lockfile, normalizes the tar
metadata, and prints the artifact digest. Review that digest in
`config/hermes-speech.json` before publication. Keep the artifact and build log
outside Git; the same source/toolchain can rebuild it. Place that artifact and
the two checksum-pinned language packages in an operator-owned mode-0700 guest
assets directory, with mode-0600 files. Do not install packages into the sealed
Hermes runtime.

Publish and validate the selected release on origin/master. On home-core, run
the guarded `setup-hermes-speech-bridge.py --revision FULL_SHA` plan, then apply
its exact reported `--expected-plan-sha256` with `--apply`. This changes only the owner speech admission in
the installed adapter. In a clean published guest checkout:

```bash
python3 scripts/setup-hermes-speech.py --revision FULL_SHA --assets /absolute/private/assets
python3 scripts/setup-hermes-speech.py --revision FULL_SHA --assets /absolute/private/assets --apply
sudo -n python3 scripts/install-hermes-dashboard-relay.py --revision FULL_SHA --surface webapp
sudo -n python3 scripts/install-hermes-dashboard-relay.py --revision FULL_SHA --surface webapp \
  --apply --expected-sha256 REVIEWED_PLAN_SHA256
./scripts/setup-hermes-guest.sh install-startup --config "$config"
```

The speech installer requires the existing fresh household backup/network gates,
the qualified owner image and the verified MCP target. It backs up config and
policy, preserves all inference, OMH, browser, household and memory settings,
installs the packaged Webapp separately, configures its native authenticated
loopback listener on 18796, and restarts only the selected Hermes sandbox through
NemoClaw. The relay changes from the admin dashboard to that Webapp only after
an unauthenticated config request returns 401 and the basic login exists.

Native forwards can disappear while the sandbox remains Ready. Startup now
runs native recovery after starting the sandbox. The source-owned user service
`homecompute-hermes-dashboard-watch.service` probes the authenticated Webapp
forward every 20 seconds and invokes native recovery if needed. It never owns
the in-sandbox gateway process. Verify the effective user boot transaction,
authenticated Webapp chat, both speech languages, audio playback/rearming,
stop phrases and restart recovery; API-only tests do not prove microphone use.

For rollback, run the same speech installer with `--apply --rollback`. It closes
the guest relay first, restores the verified private config/environment and
policy preimages, disables its watcher and restarts natively. Restore the former
relay with the published relay installer's `--revision FULL_SHA --rollback`
after verifying its native login. Keep conversations, OMH data, model caches and speech artifacts.
Undo speech before an older OMH config rollback: its full-config guard correctly
refuses a later configuration it does not own.

NemoClaw reserves container port 8646 for its API socat forwarder. Keep the
Webapp on 18796; the watcher runs `openshell forward service` for that separate
listener. Native `recover` alone forwards only NemoClaw's dashboard/API ports.
MCP headers must keep `${HOMECOMPUTE_HERMES_MCP_TOKEN}` as an environment
expression so a restart cannot retain an expired opaque handle.

For the diagnosed 6 November reserved-port incident only,
`recover-hermes-webapp-port.py` validates the complete container ID, exact
qualified image, stopped state, full mode-0600 `/sandbox` archive, config
preimage and speech receipt. It corrects only the port and deferred MCP header,
retains private before-files, and restarts the same Docker identity without
deleting native records or importing data. This exceptional repair must be
followed by native Ready/recovery, MCP, authenticated browser and normal
stop/start verification. Its normal entry point requires published source;
the incident receipt records any urgent source reconciliation.

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

## Household MCP access

See the [dated live verification](../../docs/hermes-mcp-validation-2026-11-06.md)
for applied revisions, actual client checks and remaining platform differences.

Hermes uses the existing private HTTPS HomeCompute bridge, rather than direct
credentials for Aula, Tilbudstrolden or Home Assistant. The `hermes-owner` bearer
is distinct from OpenClaw's credential and the retired `hermes` bearer, which
remains denied. OpenShell holds the credential and projects an opaque handle
only to the exact MCP endpoint. The source-owned
[household skill](skills/homecompute-household/SKILL.md) explains the tools.

The finite grant includes read-only Aula discovery, calendar, weekly letters,
posts and threads; Tilbudstrolden searches/stores/offers; public research;
existing shopping-list, discount-watch, stock, calendar and task tools; bounded
system metadata; and Home Assistant light/climate/cover proposals. Household
writes retain their revision, request-ID and explicit-owner-intent checks.
Home Assistant execution still requires separate human approval. Account/site
administration, mailbox access and infrastructure maintenance execution are
outside this grant. Future service tools require an explicit source grant.

From an exact published Git checkout on home-core, inspect and apply the narrow
bridge installer. It accepts only the reviewed installed preimages or this
release's output, retains private backups, stops only the adapter during the
file/credential transaction, and restores it on installation failure:

```bash
sudo python3 scripts/setup-hermes-mcp.py --revision FULL_SHA
sudo python3 scripts/setup-hermes-mcp.py --revision FULL_SHA \
  --expected-plan-sha256 REVIEWED_PLAN_SHA256 --apply
```

Stage that same published Git checkout in the agents guest. Pipe the export
directly over SSH to `python3 scripts/connect-hermes-mcp.py --revision FULL_SHA
--sandbox OWNER_SANDBOX`; never display or save the exported bearer. The export
command is `sudo python3 scripts/setup-hermes-mcp.py --revision FULL_SHA
--export-credential` on home-core. The selected-owner wrapper also exposes
`connect-mcp --config "$config"`, consuming the same private stdin pipe.
The standalone connector accepts the retained `hermes-personal` owner so this
integration can precede the independent 0.23 cutover. Use only the active owner;
this command does not select, recreate or upgrade a sandbox. The connector
requires fresh private household backup/network gates, uses native `mcp add`,
requires verified credential resolution and matching private target pins, and
installs the exact skill through the native skill lifecycle. Conflicting
registrations and edited skills are preserved for review. An uncertain native
add must be inspected through native status before retrying.

NemoClaw's generated Hermes MCP policy assumes the system Python or the `.venv`
path. OpenShell observes the selected 0.23 image's actual executable as
`/opt/hermes/python-runtime/bin/python3.14`. The connector adds only that exact
release-pinned path to the existing MCP route, after verifying its endpoint,
private IP, provider binding and original executable grants. It preserves every
other policy field, retains private before/after policy backups, checks for
concurrent changes, and requires full native policy readback. It neither adds a
general Python wildcard nor opens another destination. Reapply the connector if
a native MCP registration update regenerates its policy.

Verify `nemohermes OWNER_SANDBOX mcp status homecompute --json --tools`, then
exercise read-only Aula discovery, Tilbudstrolden store lookup and Home Assistant
metadata from Hermes's actual tool client. Keep household payloads private;
record only tool availability and success/failure. The tracked `probe_mcp.py`
can be piped to `nemohermes OWNER_SANDBOX exec --stdin -- python3 -`; it uses
Hermes's actual discovery and tool handlers for seven read-only source checks.
Repeat after native restart
and verify the skill and opaque MCP configuration persist. Authentication or
tool inventory alone does not prove each upstream service works.

Rollback first removes the `homecompute` registration through the native MCP
lifecycle and removes only the installed `homecompute-household` skill. On
home-core run `setup-hermes-mcp.py --revision FULL_SHA --rollback` to restore the
exact previous adapter code and credential file; later source or credential
changes block rollback. This preserves household databases, conversations and
upstream services. Remove a retained OpenShell provider only after confirming
no other sandbox still attaches it.

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
## Pinned OMH and dashboard lifecycle

`config/hermes-omh.json` selects OMH 4.0.6 at commit
`8e9263c14c34834b203135409b49c89831878501`. Build its pure Python wheel with
`python3 scripts/build-hermes-omh.py --cache /absolute/private/cache` (mode 0700).
The source archive, offline build backend and reproducible wheel are SHA-256
pinned. Extract `install.sh` from that verified archive; its checksum is pinned
separately. Transfer just the wheel and installer to an operator-owned mode-0700
guest assets directory, with mode-0600 regular files.

From a clean published release on the guest, run:

```bash
python3 scripts/setup-hermes-addons.py --revision FULL_SHA --assets /absolute/private/assets
python3 scripts/setup-hermes-addons.py --revision FULL_SHA --assets /absolute/private/assets --apply
sudo -n python3 scripts/install-hermes-dashboard-relay.py --revision FULL_SHA
# Review the plan, then apply its exact hash:
sudo -n python3 scripts/install-hermes-dashboard-relay.py --revision FULL_SHA \
  --apply --expected-sha256 REVIEWED_PLAN_SHA256
```

The guarded add-on installer requires fresh household backup/network gates and
the selected Ready image. It uses native exec stdin and the official offline
installer in an independent `/sandbox/.local/share/omh/venv`; it does not modify
the sealed `/opt/hermes` runtime. Setup uses the minimal core skills, adds the
OMH plugin, maps all 18 categories to local `automation-moe` at medium reasoning,
caps both OMH concurrency and Hermes delegation at one, and disables automatic
updates. OMH external memory and dreaming stay off: Mattermost personal scope is
not recognized by its current memory classifier. Existing Hermes memory,
Mattermost, browser, MCP and deferred model credentials must survive unchanged.
Profiles remain a later change. See the [source audit](../../docs/research/oh-my-hermes-managed-integration-2026-11-06.md).
The [6 November rollout verification](../../docs/hermes-omh-validation-2026-11-06.md)
records the applied revisions, actual client checks and remaining platform gaps.

Dashboard credentials are generated once into the existing protected guest
`~/.config/homecompute-hermes/secrets/dashboard-login.json`; never print this
file. Only the scrypt password hash and independent session signing secret enter
the private Hermes config. Native dashboard authentication, Origin/CSRF checks,
cookies and WebSocket tickets remain authoritative. The guest relay refuses to
install until the protected API returns 401 and the native basic provider exists.

The relay verifies each new OpenShell channel with an anonymous `GET /api/config`
before sending any client bytes. Transient channel-setup resets are retried up
to three times, each with an eight-second deadline. The 401 response must have
bounded, unambiguous framing and leave the same connection reusable. The relay
then forwards bytes unchanged, including WebSocket upgrades. Browser requests
are never replayed after forwarding begins; a failed chat response cannot cause
a duplicate submission. Caddy's Hermes-only `keepalive off` remains necessary
for its separate stale-idle-socket failure mode. The bridge bind and all native
authentication, Origin, CSRF and WebSocket gates remain unchanged.

The installer accepts only the exact previous socat unit or its rendered managed
unit, pins a root-owned relay copy to the published revision, verifies the systemd
unit, retains its private preimage and restores it if activation fails. It
preserves the existing admin dashboard (18794) or Webapp (8646) target during a
relay repair. A new installation defaults to the Webapp. Only an explicit
`--surface webapp` or `--surface admin` plan/apply changes that selection, after
native authentication at the selected port passes. This keeps a relay repair
independent of the speech lifecycle and its Webapp qualification. It
restarts only the guest relay; existing dashboard streams may reconnect. It does
not restart the sandbox, Caddy, Mattermost or the VM. To restore the retained
relay unit without changing application data, run the same published installer
with `--revision FULL_SHA --rollback`. Later unit edits block rollback for review.

On home-core, stage the same published source under
`/srv/homecompute/releases/FULL_SHA`, run
`sudo python3 scripts/setup-hermes-dashboard-ingress.py --revision FULL_SHA`,
review its two config hashes, then add `--apply --expected-caddy-sha256 HASH
--expected-homepage-sha256 HASH`. This guarded lifecycle builds only the two
canonical Nix relay/firewall units, preserves unrelated routes and maintenance
markers, briefly restarts Caddy (its admin API stays disabled), and changes the
existing Hermes frontpager card to `https://agents.home.arpa/hermes/`. The only admitted
bridge client is Caddy; native auth is required on approved LAN/Tailscale access.
Verify Mattermost reconnects after this restart. No new public listener, model
route or OpenClaw state is introduced.

The pinned web build preloads lazy sidebar chunks at root-relative `/assets/`
URLs. Caddy redirects only hashed JavaScript/CSS chunk names to the existing
`/hermes/` route. Office's image/JSON assets and root API retain their own
authentication and routing. Without this compatibility redirect, each sidebar
preload reaches Office's Basic-auth challenge and opens another browser login
dialog even though the Hermes cookie is valid. Verify sidebar navigation after
login, including Models, Config and Sessions, plus the Office asset/API boundary;
the login page and initial dashboard alone do not exercise lazy preloading.

Verify native plugin discovery (OMH enabled, no error, tools and hooks registered),
local routing/concurrency readback, authenticated dashboard login and chat,
Mattermost DM response, browser public navigation and private-address denial,
MCP status, sealed package-manager verification, and a native stop/start. Keep
private logs, config backups and the source-linked receipt in the sandbox assets
directory; these are evidence, not Git desired state.

The published read-only probes can be run with the operator's native CLI:

```bash
export OPENSHELL_GATEWAY=nemoclaw NEMOCLAW_GATEWAY_PORT=8080 NEMOCLAW_NO_AUTO_AUDIT_LOGS=1
nemohermes hermes-personal-v23 exec --stdin -- /opt/hermes/.venv/bin/python - < deploy/hermes/omh/verify.py
nemohermes hermes-personal-v23 exec --stdin -- /opt/hermes/.venv/bin/python - < deploy/hermes/omh/probe_runtime.py
```

The runtime probe calls `omh_status`, verifies the sealed packages, opens only
the public example page in a named browser session, and requires a private-address
navigation to be rejected. It emits booleans and a public page title only.

Rollback closes the guest ingress before restoring only the verified private
config preimage and restarting natively:
`python3 scripts/setup-hermes-addons.py --revision FULL_SHA --assets
/absolute/private/assets --apply --rollback`. It retains all OMH and Hermes data.
The HTTPS/card source can then be reverted through the core ingress lifecycle;
retain its private before-files and receipt. Never expose the restored loopback
dashboard through an active relay without its native public authentication.
