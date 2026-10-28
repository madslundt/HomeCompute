# Specialist MCP and API integrations

This implements the selected [assessment](research/mcp-and-skills-assessment-2026-10-28.md)
through the existing authenticated HomeCompute MCP endpoint. Context7 and Maps
use their official remote MCP services. Expedia, Immich and n8n use fixed,
read-only API routes in the trusted facade, avoiding redundant MCP services.
GitHub stays in the coding broker's `gh` workflow. Paperless is deferred: no
document-storage service or document corpus is connected by this change.

## Roles and scope

| Agent | Added tools |
| --- | --- |
| Apollo (`engineer`) | Public Context7 library resolution and documentation |
| Orion (`operator`) | Context7 documentation and registered public n8n workflow status |
| Nova (`researcher`) | Registered public n8n workflow status |
| Lyra (`travel`) | Maps places/routes/weather, Expedia hotel discovery and public workflow status |
| Luna (`personal`) | Owner-private Immich photo search, metadata and asset links only |
| Vega (`shopper`) | Maps places search for shops and opening information |
| Atlas, Ceres, Hestia, Themis, Plutus, Gaia | No additional domain tools |

The six selected specialists also receive `integration_status`. Atlas delegates
domain requests; Family receives no private photo or external-provider grants.
Native role policy narrows the shared OpenClaw credential; it is not a separate
cryptographic identity per specialist. Scope is enforced by the native tool
ceilings and explicit per-role allows/denies. Providers cannot choose roles.

## Configuration and credentials

[`config/openclaw-integrations.json`](../config/openclaw-integrations.json)
owns enablement, hourly call budgets and exact public workflow IDs. Runtime
reads the copy in the selected core communication release, not an editable UI
setting. Empty workflow IDs cannot select arbitrary workflows. Use only public
Travel/Sweet deals/news workflows; private school/mail workflows are outside
this grant. The n8n tool returns execution status/times only, never workflow
nodes, credentials, execution payloads or report contents. It runs no jobs.

Dedicated provider tokens are external files under
`/var/lib/homecompute-openclaw/config/integration-PROVIDER-token`, where
`PROVIDER` is `context7`, `maps`, `expedia`, `immich` or `n8n`. Provision files
through the operator's secret custody path, owned by `homecompute-openclaw`,
mode 0600, single regular file, with no symlinks. Do not paste keys into chat,
commit them or pass them on command lines. The facade never returns key paths
or values. An absent Context7 key permits its anonymous service; other
providers require their own keys. Use an owner-specific Immich key limited to
asset search/read, and an n8n key with only the workflow/execution reads the
installed edition permits. Neither provider receives another provider's key.

Maps is disabled by default. Before setting `enabled` in source, qualify its
[content retention, attribution and compatible-model requirements](https://developers.google.com/maps/ai/grounding-lite).
The facade does not index or archive Maps output, but native chat, tool
transcripts, memory and backup retention still require a compatible policy.
The current persistent chat environment has not been qualified for this use;
do not treat merely supplying a key as sufficient. Budget/billing and key
restrictions also need operator configuration.

Expedia API eligibility, authentication and current inventory require a live
check with the issued key. Its public MCP implementation is not installed:
the facade implements only its documented hotel recommendation GET route with
a deadline, no retries, no raw diagnostics and no request/header logging.
Traveler count does not encode child ages, room occupancy or member pricing;
recommendations are leads until a provider establishes a matching full quote.
No booking, reservation, payment or account access tool exists.

The photo tool calls only the existing local Immich smart-search route. Its
response excludes originals, EXIF, face information and file paths. Returned
asset links use the LAN service address; following a link still requires the
owner's normal Immich access. This uses Immich's own search model, not the
Harrier document embedding route.

## Installation and qualification

1. Review the exact provider catalog and tool/role diff. Publish a reviewed
   commit on `origin/master` before using it as a release. Existing unrelated
   uncommitted work must remain outside this release.
2. Apply that release's guarded `setup-openclaw-core-communication.py --install
   --source RELEASE` lifecycle on home-core. The bundle includes the provider
   facade and catalog. Use its existing service activation/restart procedure
   during a quiet window, preserving the previous bundle and private state.
3. In the managed sandbox, run the selected release's
   `scripts/setup-openclaw-integration-tools.py` as the native config owner.
   It first dry-runs the native schema and prints `source_config_sha256` plus
   the exact role grants. Apply with `--apply --expected-config-sha256 HASH`.
   It retains a private before-config backup and changes only agents/tools.
   Stage repository-owned modules together (`openclaw_integration_policy.py`,
   `openclaw_team_policy.py` and their existing policy dependencies), using the
   existing managed sandbox tooling procedure. No automatic gateway restart
   occurs. Reapplying the team installer preserves an intact integration grant;
   a partial or revoked grant is refused rather than restored.
4. Reconcile the updated managed skills and identity instructions through the
   existing team installer with its source/config/skill hash checks. Restart
   through the supported native lifecycle and refresh MCP discovery.
5. From actual specialist sessions, check `integration_status`, then one
   permitted call. Verify an unrelated specialist and Family cannot invoke
   that tool, and Atlas delegates. Test an unavailable key without disclosing
   upstream responses. A native grant or `configured` status alone does not
   establish a successful provider request.

Requests use fixed origins, no proxy environment or redirects, finite inputs,
bounded responses and one deadline. The SQLite hourly ledger stores only
provider name, UTC hour and call count; budgets survive adapter restarts.
Failed provider attempts consume budget too. No request text, key, response
or vector is stored there. No provider output enters the research archive.

Rollback the native grants from the retained before-config through supported
native tooling, restore the prior communication release and restart affected
services. Keep private source data and existing retrieval indexes intact.
Revoking a provider key independently withdraws that provider's access.

## Embeddings and storage

The existing `embedding` route serves pinned
`microsoft/harrier-oss-v1-0.6b`, 1024-dimensional normalized embeddings, through
the private gateway. It supports native per-agent memory and finite retrieval
for school, research, operations and household/workflow matching. The model
computes vectors; the source owners store the records and vectors.

The live read-only check on 2026-10-28 observed the running pinned CPU TEI
container and roughly 900 requests since its metrics reset. Core SQLite
indexes contained 212 embedded school passages, 285 operations passages, 14
battery workflow passages, 10 news passages and 9 shopping passages. The public
research index inspected was empty. These counts establish persisted use, not
that every consumer or future search currently succeeds.

Existing private SQLite files under the core configuration/communication
retrieval directories store passages, provenance, expiry and the exact model
revision alongside vectors. Keep principal/domain indexes separate and files
private. Model-weight storage is rebuildable; source records, deletion state
and their recovery policy need durable storage. Re-embed when model identity or
dimensions change. A separate vector server is unnecessary at this observed
scale; introduce one only for a measured capacity/concurrency need.

The separate pending application-recovery tooling inspected in the operator
working tree includes core OpenClaw config/communication state in a quiesced
archive and checks restored SQLite integrity. It is outside this integration
release; this change does not establish its publication or deployment. The observed active backup timer covered the
agents VM bootstrap; that alone does not prove backup coverage for core
retrieval files. Successful off-host backup and restore of these indexes and
the source/deletion records remain unverified. No backup job or disruptive
restore drill was run in this task.

## Verification status

The new adapter completed live anonymous Context7 library resolution and a
public Python documentation query from the workstation on 2026-10-28. This
verifies the remote adapter path, not installed specialist sessions. Expedia,
Maps, Immich and n8n credentials were not provisioned by this task. Native
activation, provider access and post-release role denials still require the
qualified rollout above.

Focused provider, grant, team, installer, transport and roster checks passed
(68 tests). The repository validator passed its test phase after correcting a
stale existing Lyra research-search assertion, then stopped at an unrelated
compute SSH tunnel guard: the untouched source forwards automation to port
18400 while the validator expects 18300. That mismatch was reproduced on the
pristine base revision. Later validation stages were not reached. No production
rollout was performed.
