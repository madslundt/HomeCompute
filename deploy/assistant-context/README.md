# Private assistant context

Source candidate for the [assistant plan](../../docs/proactive-personal-assistant-plan.md),
[ADR-029](../../docs/adr/029-proactive-assistant-context.md) and
[issue 61](https://github.com/madslundt/HomeCompute/issues/61). Disabled by
default; no production installation or household readiness is claimed.

## Boundaries

The Nix module runs the service on home-core as `homecompute-context`, with
private state at `/srv/state/assistant-context` and loopback `127.0.0.1:18816`.
Importing it opens no LAN listener, Caddy route, source credential, model route,
scheduled workflow or device action. Source artifacts, existing task/reminder
stores, OpenClaw execution and Home Assistant approval retain their authority.

`config/assistant-attention.json` defaults to shadow attention. Ordinary source
snapshots become digest candidates; meaningful prepare/notify rules require
source-owned event types. Freshness, quiet hours, occurrence suppression and
notification budgets are deterministic. Source text cannot grant urgency,
permissions or tools. A reviewed improvement candidate is not deployed code.

Journal search is initially bounded lexical retrieval. Existing Harrier and
workflow retrieval remain unchanged; hybrid indexing of this journal requires
a separately qualified integration. Source coverage is partial, not a complete
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
| assistant | `POST /v1/tools` | Execute finite `{name, arguments}` context tools |
| operator | `POST /v1/review` | Recover pending evaluation; body `{}` |
| any authenticated role | `GET /v1/health` | Read mode/readiness metadata |

Use bearer authentication, one bounded JSON body and no browser Origin.
The assistant has `homecompute_context_search`, `homecompute_context_today`,
`homecompute_context_source`, `homecompute_context_decision`,
`homecompute_context_feedback` and `homecompute_context_memory`. It cannot ingest
events, approve memory or candidates, select origins, or change action policy.
Memory proposals remain inferred ordinary candidates for human review; lexical
sensitivity checks do not detect every sensitive inference.

## Durability and deletion

Accepted updates atomically write a journal entry, current projection and
metadata-only pending evaluation. Identical retries return the prior receipt;
changed content at the same revision refuses; older arrivals cannot replace
current data. Evaluation acknowledges pending work only after durable decision
admission. Restart/replay cannot create a second decision for that occurrence.

Delete events scrub online source payloads and derived memory. Attention purges
linked feedback/candidates, retaining scrubbed held delivery metadata to prevent
replay. Only a strictly newer source revision can recreate a deleted object.
Source connectors must distinguish genuine recreation from backup replay.
Restore tombstones before retrieval and replay; backups retain their own bounded
expiry policy.

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
./scripts/validate-repository.sh
git diff --check
```

Daythread provider changes require their separate API/settings/profile checks.
Synthetic tests and Nix evaluation do not prove household or phone readiness.

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

New state and connector cursor files are not automatically in the existing NAS
archive. Before household admission, add them to the owning recovery source,
stop the specific collectors/service for consistent capture, include external
credential custody, and restore-test in isolation. Never copy active SQLite
files without their WAL or a consistent SQLite backup operation.

Pause the specific collector/context service independently of normal chat,
task timers and HA. Roll back service activation through its source declaration
and normal release; retain application state/tombstones. The bridge installer
restores only its verified exact after-state and refuses later edits. Do not
replace task databases or conversation history to undo context code.

After an authorized rollout inspect `homecompute status`, `services`, `doctor`
and `drift`, installed source/configuration, and the actual authenticated client
path. Report applied source/lifecycle and remaining divergence. This source-only
candidate does not establish synchronized systems.
