# Private assistant context

Source candidate for the [assistant plan](../../docs/proactive-personal-assistant-plan.md),
[ADR-029](../../docs/adr/029-proactive-assistant-context.md) and
[issue 61](https://github.com/madslundt/HomeCompute/issues/61). Disabled by
default; no production installation or household readiness is claimed.

## Boundaries

The Nix module runs the service on home-core as `homecompute-context`, with
private state at `/srv/state/assistant-context` and loopback `127.0.0.1:18826`.
Importing the disabled module starts no service or job. The communication relay
has one exact private routine path, restricted to the existing n8n source IP;
the context backend separately requires a routine credential. Source artifacts, existing task/reminder
stores, OpenClaw execution and Home Assistant approval retain their authority.

`config/assistant-attention.json` defaults to shadow attention. Ordinary source
snapshots become digest candidates; meaningful prepare/notify rules require
source-owned event types. Freshness, quiet hours, occurrence suppression and
notification budgets are deterministic. Qualified active notify decisions are
dispatched automatically after source coverage/review, using bounded factual
source excerpts and the standing delivery grant; all default snapshot rules
remain digest-only. Accepted or uncertain sends are never retried automatically. Source text cannot grant urgency,
permissions or tools. A reviewed improvement candidate is not deployed code.

Journal search supports bounded scope-filtered Harrier/lexical rank fusion,
with explicit lexical fallback on embedding failure. Its embedding credential
is separate from source collectors. Existing workflow retrieval is unchanged.
Source coverage is partial, not a complete
archive of recordings, school history, calendars or home telemetry.

## Credentials and API

Provision an external root-owned mode-0600 client declaration using
[the schema](../../config/assistant-context-clients.example.json). Replace each
placeholder with an independent random token outside Git. Each credential binds
an actor, role, principal/domain/visibility and finite source allowlist. Request
content cannot choose these values. Initial scopes are owner/personal/private
and expressly selected family/household/shared; personal memory is owner-only.
Work and partner scopes remain denied. Before admitting Daythread, verify its
fixed source profile contains only authorized personal data; profile binding
cannot classify mixed personal/employer recordings.

| Role | Endpoint | Effect |
| --- | --- | --- |
| collector | `POST /v1/events` | Admit one exact canonical event within its source grant |
| collector | `POST /v1/coverage` | Record `{source, observed_at, complete}` after scan admission; review pending events |
| collector | `POST /v1/coverage/failure` | Mark one source unavailable without replacing its last successful scan |
| assistant | `POST /v1/tools` | Execute finite `{name, arguments}` context tools |
| operator | `POST /v1/review` | Recover pending evaluation; body `{}` |
| routine | `POST /v1/routines/run` | Admit only an allowlisted existing routine with `{routine_id, scheduled_for}` |
| any authenticated role | `GET /v1/health` | Read mode/readiness metadata |

Use bearer authentication, one bounded JSON body and no browser Origin.
The assistant has `homecompute_context_search`, `homecompute_context_today`,
`homecompute_context_source`, `homecompute_context_decision`,
`homecompute_context_feedback`, `homecompute_context_memory`,
`homecompute_context_briefing`, `homecompute_context_routine`,
`homecompute_context_delegate` and `homecompute_context_improvements`. It cannot ingest
events, approve memory or candidates, select origins, or change action policy.
Memory proposals remain inferred ordinary candidates for human review; lexical
sensitivity checks do not detect every sensitive inference.

Briefing, routine, delegation and improvement tools require the full owner-private
source grant. A separate `routine` client has a finite `routines` allowlist and
cannot call chat tools. The service derives each occurrence from the retained
routine and Copenhagen local day, accepting only a near-current workflow timestamp.
The optional assistant `origin_id` is an operator-bound logical conversation
handle; model arguments cannot choose an origin, native role or destination.

## Briefings, native work and improvement

Briefings report calendar overlaps, possible Daythread follow-ups, source coverage
and cited cross-source interpretations. Per-source freshness budgets apply.
Optional local model output must cite current supplied source revisions; valid
citations do not independently prove that an interpretation follows from them.
Deterministic fallback remains available. No briefing executes a device action.

The two routine IDs retain existing n8n owners: `morning-overview`
(`WxRovre260928abc`) and `tomorrow-preparation` (`WKu7Zv4dE4ztnaRY`). There is no
second morning/evening scheduler. A separate standing owner-DM delivery grant is
required; claim-before-send occurrence state distinguishes accepted, delivered
and uncertain. Lost replies never authorize a fresh occurrence. See the
[patch contract](../../automations/assistant-context/briefing-patch-contract.md).
The live owner edge, audience and existing guards must be qualified before a
source patch can consolidate delivery.

Native OpenClaw work uses the dedicated no-tool `assistant-context-worker`,
an independent SSH key, fixed guest transport and the pinned native RPC contract.
The API returns its actual receipt/result to the requesting chat tool; asynchronous
push into a particular Mattermost root thread is not implemented by this bridge.
Native timeouts do not mean cancellation. See the
[worker lifecycle](../openclaw/assistant-context-worker/README.md) before enablement.

The improvement controller is a separate root-owned job. It accepts only bounded
presentation changes and stricter correlation thresholds in
`config/assistant-briefings.json`. It independently evaluates candidates, performs
synthetic held-out canary replay, checks an external standing class grant and
queues a protected review PR. It never merges its own PR. Reconciliation verifies
the exact merged head, required CI and published revision; optional automatic
apply then uses the guarded root-owned policy installer. Publication, merge,
reconciliation and activation are distinct states. Wider workflow, skill, model,
permission and code changes still need their existing reviewed lifecycle. See
the [controller contract](../../automations/assistant-context/improvement-controller-contract.md)
and [managed policy lifecycle](../../automations/assistant-context/improvement-policy-apply-contract.md).

## Durability and deletion

Accepted updates atomically write a journal entry, current projection and
metadata-only pending evaluation. Identical retries return the prior receipt;
changed content at the same revision refuses; older arrivals cannot replace
current data. Evaluation acknowledges pending work only after durable decision
admission. Restart/replay cannot create a second decision for that occurrence.

Delete events scrub online source payloads, derived memory and vector caches.
Attention purges linked feedback/candidates; routines and delegation scrub linked
local derived state, retaining scrubbed held delivery metadata to prevent
replay. Only a strictly newer source revision can recreate a deleted object.
Source connectors must distinguish genuine recreation from backup replay.
Restore tombstones before retrieval and replay; backups retain their own bounded
expiry policy. The pinned OpenClaw lifecycle archives ordinary session transcripts;
local purge leaves `purge_pending` until the native retention/deletion lifecycle
is verified. It does not claim erasure from native archives or old backups.

See [source sync](../../automations/assistant-context/README.md) for actual source
contracts, source revision state, partial pages and authenticated Daythread
export. Run collectors from the selected published checkout as their existing
source-owning identity, rather than granting those credentials to the context
server. Coverage is posted after event admission. Unknown or stale sources are
reported explicitly, never interpreted as an empty day.

## Validation

```sh
python3 tests/assistant-context-store-test.py
python3 tests/assistant-context-sources-test.py
python3 tests/assistant-attention-test.py
python3 tests/assistant-context-api-test.py
python3 tests/assistant-context-bridge-test.py
python3 tests/assistant-context-delivery-test.py
python3 tests/assistant-context-retrieval-test.py
python3 tests/assistant-context-briefings-test.py
python3 tests/assistant-context-delegation-test.py
python3 tests/assistant-context-worker-ssh-test.py
python3 tests/assistant-context-recovery-test.py
python3 tests/assistant-context-improvements-test.py
python3 tests/assistant-context-workflow-test.py
python3 tests/assistant-context-phases-test.py
./scripts/validate-repository.sh
git diff --check
```

Daythread provider changes require their separate API/settings/profile checks.
Synthetic tests and Nix evaluation do not prove household or phone readiness.

Source verification on 2026-11-06: the full repository validator passed,
including containerized Nix checks with the optional context features enabled.
The 15 assistant suites cover 178 cases; the Linux-only permission regression
was verified separately in a Linux container. Daythread export/profile tests
passed 25 cases and targeted Ruff passed. Environment-dependent optional skips
remain explicit in validator output. No production activation, private-provider
read or real notification was performed.

## Enablement

Read [operations](../../docs/homecompute-operations.md),
[Git deployment](../../docs/git-deployment.md) and
[NixOS operations](../../docs/nixos-operations.md). Establish current installed
revisions/hashes, source coverage, exact bot audience, secrets and recovery.

Start with synthetic data. Add a reviewed host declaration enabling
`homecompute.assistantContext.enable`; its default credentials path is
`/etc/homecompute/assistant-context-clients.json`. systemd `LoadCredential`
keeps secrets out of the Nix store. Household admission additionally requires
`dataClassification = "household"` and a truthful `restoreVerified = true`.
Those declarations do not themselves extend NAS coverage or perform a restore.

Optional declarations remain disabled: `inference.embedding`,
`inference.briefings`, `delegation.enable`, `delivery.enable`, `sync.enable`,
`improvements.enable` and `improvements.applyReviewed`. Delivery additionally
requires `mode = "active"`; active apply requires `managedBriefings = true`.
Initialize that root-owned file with `setup-assistant-context-policy.py` before
selecting it. Its default source-backed configuration remains sealed.

Inference uses fixed verified HTTPS `ai.home.arpa` routes, dedicated external
tokens (despite the historical `.json` suffix these contain only a bearer token),
and a service-private hosts mapping to the declared private IPv4. Provision the
private CA in system TLS trust before qualifying calls. The context service gains
only that gateway IP and, when enabled, the fixed agents VM IP. Worker key custody
is `/etc/homecompute/assistant-context-worker/{id_ed25519,known_hosts}`; delivery
credential custody is `/etc/homecompute-assistant-context/delivery-auth.json`.

The embedding and briefing routes use separate, non-expiring LiteLLM keys. The
embedding key is limited to model `embedding` and owner
`assistant-context-embedding`; the briefing key is limited to `automation-moe`
and owner `assistant-context-briefing`. Provision them from the exact published
HomeCompute release on home-core as root with
`scripts/setup-assistant-context-inference-credentials.py plan --revision FULL_SHA`,
review the emitted state and hash, then apply with the same revision and
`--expected-plan-sha256 PLAN_HASH`. The helper preserves valid partial installs,
refuses mismatched existing scopes or unsafe files, and creates only missing
files at the two declared `/etc/homecompute/assistant-context-{embedding,briefing}.json`
paths. It does not change routes, service configuration or running processes.
Synthetic contract coverage is `python3 tests/assistant-context-inference-credentials-test.py`.

Before each LiteLLM create request the helper durably writes a root-only
`<credential-path>.pending` intent. If create, readback or file installation
fails, the intent remains and future plan/apply commands stop. Do not remove it
to retry. Inspect the exact pending owner/model in LiteLLM administration. If a
matching key is still present and the credential file exists, resolve only after
the helper verifies that key with `--resolution key-verified`. If the file is
absent, revoke only the orphan key for that exact owner/model in LiteLLM, then
record that operator action with `--resolution orphan-revoked`. Both commands
require the pending marker's reviewed SHA-256 and preserve a resolved receipt:

```sh
sha256sum /etc/homecompute/assistant-context-embedding.json.pending
python3 scripts/setup-assistant-context-inference-credentials.py resolve-pending \
  --name embedding --revision FULL_SHA \
  --expected-pending-sha256 PENDING_HASH --resolution orphan-revoked
```

Use `--name briefing` for the briefing key. After `orphan-revoked`, create a new
plan before applying. The helper cannot verify the external revocation decision;
that step must be completed and reviewed by the operator.

The optional collector timer runs existing source readers as
`homecompute-openclaw`; provision that identity and its existing scoped bridges
through their owning lifecycle first. Build its private configuration from
[`assistant-context-sync.example.json`](../../config/assistant-context-sync.example.json)
using the [guarded source-selection installer](source-sync.md) and select only
the intended calendar aliases. Calendar windows can use an exact
`start`/`end` pair or a bounded rolling window; the example rolls from one day
before through seven days ahead in Copenhagen local time. Its configuration and
revision ledger stay under `/var/lib/homecompute-assistant`. The improvement timer executes only the
selected published release under `/srv/homecompute/current`, requires separate
root-controller credentials and a standing grant, and runs no chat-controlled
code. Six-hour improvement scans and 15-minute source scans do not own briefing
schedules. See `config/assistant-improvements.json` for fixed custody paths.

Publish/review the full SHA, then use the owning release lifecycle:

```sh
./scripts/homecompute deploy home-core --revision FULL_SHA
```

Verify actual listener/user/file modes and authenticated health without putting
tokens in argv, logs or shell history. Provision the independent assistant-role
token at `/var/lib/homecompute-openclaw/config/assistant-context-token`. Its
client declaration follows [this schema](../../config/assistant-context-client.example.json).
Use the narrow `setup-assistant-context-bridge.py` plan/hash-bound apply and
rollback from the selected published source. It preserves native bot/MCP tokens
and does not grant context to OpenClaw specialists. Install the source-owned
`homecompute-assistant-context` skill via the native skill lifecycle.

Qualify a synthetic Mattermost query/source reply after restart; wrong-role,
scope, deletion and stale-source denials; and uncertain delivery behavior.
Consolidate existing n8n notification ownership only after acceptance. Critical
HA alerts stay independent. Notification route/network registration and real
owner-audience delivery need their own verification before active operation.

## Recovery and rollback

`application-recovery.py` captures optional assistant state, source cursors,
root-controller state, managed policy and declared credential originals when
present. It quiesces loaded assistant jobs/services and resumes only previously
active units. Old captures with the feature absent remain supported. Private
credentials belong only in the encrypted recovery archive, never Git.

Use `assistant-context-restore.py` for an isolated staged restore. Staging writes
a pending fence before serving can resume. Completion requires separately trusted,
hash-pinned current deletion evidence and a recent source-controller admission
outside the old archive; the archive cannot attest its own freshness. Replay
tombstones, scrub derived ledgers and invalidate vectors before the fence becomes
ready. An export alone is explicitly unverified. Missing proof keeps the service
fenced. Follow the owning application recovery lifecycle to install a verified
restored tree; the isolated helper does not replace live state. Restore service
UID/group ownership and private directory/database/fence modes before launch.
A stable source-reference memory tombstone remains blocked even after source
recreation; rebuilding that memory requires an independently reviewed versioned
reference. This conservative loss does not restore deleted memory content.

See the [empty synthetic-only recovery admission procedure](recovery-admission.md)
for the root-owned, hash-bound proof path and its scope limits.

The context database uses schema 4 for durable cleanup and monotonic deletion
floors. Earlier code refuses that schema; a code downgrade alone cannot reverse
the migration. Use compatible code or the verified prior backup with current
deletion replay, keeping restored service state fenced during recovery.

Pause the specific collector/context service independently of normal chat,
task timers and HA. Roll back service activation through its source declaration
and normal release; retain application state/tombstones. The bridge installer
restores only its verified exact after-state and refuses later edits. Do not
replace task databases or conversation history to undo context code.

After an authorized rollout inspect `homecompute status`, `services`, `doctor`
and `drift`, installed source/configuration, and the actual authenticated client
path. Report applied source/lifecycle and remaining divergence. This source-only
candidate does not establish synchronized systems.
