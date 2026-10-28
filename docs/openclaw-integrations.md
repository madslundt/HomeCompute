# Specialist MCP and API integrations

This implements the selected [assessment](research/mcp-and-skills-assessment-2026-10-28.md)
through the existing authenticated HomeCompute MCP endpoint. Context7 uses its official remote MCP service. Places and routes use
Geoapify, and weather uses Open-Meteo. SerpApi hotel search, Immich and n8n use fixed,
read-only API routes in the trusted facade, avoiding redundant MCP services.
GitHub stays in the coding broker's `gh` workflow. Paperless is deferred: no
document-storage service or document corpus is connected by this change.

## Roles and scope

| Agent | Added tools |
| --- | --- |
| Apollo (`engineer`) | Public Context7 library resolution and documentation |
| Orion (`operator`) | Context7 documentation and registered public n8n workflow status |
| Nova (`researcher`) | Registered public n8n workflow status |
| Lyra (`travel`) | Geoapify places/routes, Open-Meteo weather, SerpApi hotel discovery and public workflow status |
| Luna (`personal`) | Owner-private Immich photo search, metadata and asset links only |
| Vega (`shopper`) | Geoapify places search for shops and opening information |
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
`PROVIDER` is `context7`, `geoapify`, `serpapi`, `immich` or `n8n`. The stable
`maps` tool/status provider uses the Geoapify token file, and `hotels` uses
the SerpApi file. Old Google Maps and Expedia keys are never reused or forwarded.
Provision files through the operator's secret custody path, owned by `homecompute-openclaw`,
mode 0600, single regular file, with no symlinks. Do not paste keys into chat,
commit them or pass them on command lines. The facade never returns key paths
or values. An absent Context7 key permits its anonymous service; other
providers require their own keys. Use an owner-specific Immich key limited to
`asset.read`. Neither provider receives another provider's key.

### Token file names

All files are on **home-core**, under
`/var/lib/homecompute-openclaw/config/`:

| Provider | Filename |
| --- | --- |
| Immich | `integration-immich-token` |
| n8n (scoped key only) | `integration-n8n-token` |
| SerpApi (hotel search) | `integration-serpapi-token` |
| Context7 (optional) | `integration-context7-token` |
| Geoapify | `integration-geoapify-token` |
| Open-Meteo | No key required for the non-commercial endpoint |

Keep files owned by `homecompute-openclaw`, mode 0600. The hidden terminal
entry procedure used for Immich applies to the other files by changing only
the filename. Creating a file provisions a secret; it does not activate an
unpublished adapter or bypass that provider's qualification. The n8n scope
restriction below still applies.

### Create credentials in this order

Start with Immich; the other providers do not all need new keys now:

1. Open the [local Immich account](http://192.168.30.122:2283/), sign in as the
   owner whose library Luna may search, then select the top-right user menu,
   **Account Settings**, and **API Keys**. Create a key named
   `HomeCompute OpenClaw photo search` with only `asset.read`. The pinned
   [smart-search controller](https://github.com/immich-app/immich/blob/v3.2.4/server/src/controllers/search.controller.ts)
   requires that permission; image downloads, uploads, writes and administrative
   permissions are unnecessary. Save the displayed value directly in the
   owner's password manager under a distinct entry with that name. Account
   keys are created in [user settings](https://docs.immich.app/features/user-settings/).
2. For n8n, first check whether **Settings → n8n API → Create an API key**
   offers scoped permissions. If it does, a dedicated key for this facade needs
   `workflow:read` and `execution:list`, with an explicit expiration. Current
   [n8n documentation](https://docs.n8n.io/connect/n8n-api/authentication.md)
   limits API-key scopes to Enterprise; non-Enterprise keys have the account's
   full capabilities. If scope selection is absent, leave this adapter
   unprovisioned. Do not create an owner-wide key for status reads or upgrade
   the plan just for this tool. Existing trusted infrastructure observations
   remain available; a separate finite local status bridge would be the next
   implementation option.
3. For hotel search, create a self-service [SerpApi account](https://serpapi.com/users/sign_up)
   and copy its private API key from the dashboard to the owner's password manager.
   This replaces the Expedia adapter; no Expedia partnership is needed.
   [Current pricing](https://serpapi.com/pricing) lists 250 free searches per month.
   Start with the free plan; do not enable paid automatic renewal for this tool.
   The local 12-attempt hourly budget does not reserve or track the account's
   monthly credits, which may also be consumed by other clients. Exhausted
   credits end the attempt; the adapter neither retries nor buys capacity.
   Save the key in `integration-serpapi-token` on home-core, as described below.

Context7 already passed anonymous requests; an account key is optional for
its limits. The maps tools now use Geoapify and Open-Meteo as described below; no
Google API key is needed. Harrier uses the existing private embedding route and needs no new
provider key for this change.

Creating a password-manager entry does not automatically connect it to the
facade. On the reviewed rollout, provision the selected secret through a
private terminal/custody flow into its fixed token file. For Immich that is
`/var/lib/homecompute-openclaw/config/integration-immich-token`, with the owner
and mode specified above. Share only the entry name or external secret
reference in chat, never the displayed API-key value.

The source catalog selects only the `geoapify` maps backend. Create a project
at [Geoapify MyProjects](https://myprojects.geoapify.com/), then obtain a project
API key from its API Keys page. Geoapify currently offers a
[free tier](https://www.geoapify.com/pricing/); no paid plan is required to
qualify these bounded household reads. Restrict the key to the core's public
egress IP if that address is stable and the account supports the restriction.
Do not apply browser-referrer restrictions to server-side requests.

Named place/address lookup uses Geoapify geocoding. For category discovery,
use `places_search` with the city/area in `query`, a category (`restaurants`,
`cafes`, `shops`, `parks`, `attractions` or `hotels`) and optionally a place
`name`. Results are limited to five within 5 km. Routes resolve two explicit
locations and return only driving/walking distance and duration, without route
geometry or navigation. Ambiguous or low-confidence geocoding returns
`clarification_required` before any follow-up search, routing or weather call.
The same role grants and tool names remain in use; no native permission
expansion is needed for the provider replacement.

[Geoapify geocoding](https://www.geoapify.com/geocoding-api/) explicitly supports
storing its results. Preserve returned Geoapify, OpenStreetMap and datasource
attribution. Places coverage, opening hours and current business information
still require provider verification; this is not a Google reviews replacement.
[Places API](https://apidocs.geoapify.com/docs/places/) and
[routing API](https://apidocs.geoapify.com/docs/routing/) contracts are fixed in
`openclaw_places.py`. HTTP calls are serialized at under four per second;
existing source-owned hourly tool budgets also apply. The API key appears in
Geoapify's required query parameter; URLs, errors and key values are never
logged or returned by the facade.

Weather uses the existing
[Open-Meteo forecast API](https://open-meteo.com/en/docs). Its
[non-commercial terms](https://open-meteo.com/en/terms) cover personal home
automation; this endpoint needs no additional API key. Requests send only
resolved coordinates and the requested local date, never the Geoapify key.
Preserve Open-Meteo/CC BY attribution. Both providers are external services,
not local computation. The facade still does not archive their responses.

Hotel discovery uses the fixed [SerpApi Google Hotels API](https://serpapi.com/google-hotels-api)
through `openclaw_hotels.py`. This uses Google Hotels search data through
SerpApi, separately from Google Maps; it needs no Google API key. The stable
provider ID is `hotels`, with source-selected backend `serpapi`. The old Expedia
endpoint is removed; an `integration-expedia-token` file is never read.

Hotel requests require dates (1–31 nights), adults, a list of child ages and a
currency. Use `child_ages: []` only for an explicitly adult-only request. Infants
(age 0) are submitted as age 1 per SerpApi's convention, and both requested and
submitted ages are returned. Send only public destinations, date/count/age
parameters; no names, birthdays, calendar contents or private prompts. SerpApi
is an external search processor. Its default cache may be up to one hour old,
and its documentation reserves no-storage ZeroTrace mode for Enterprise;
this facade's no-archive policy does not imply upstream deletion.

One GET returns at most five properties. There is no pagination or property-detail
follow-up. Returned search parameters must match the requested stay and group.
Nightly and stay rates retain separate displayed, before-tax and after-tax labels;
a missing total remains unknown. Provider processing time is kept distinct from
the facade's retrieval time. Search rates are advertised leads, never confirmed
room allocation, board, member discounts or whole-family checkout quotes.
Result links are untrusted HTTPS evidence and are not fetched by the facade.
No booking, reservation, payment or account access tool exists. Errors, request
URLs and the required query-string API key are never logged or returned.

To provision the key without showing it in command history, connect to home-core
and enter it at the hidden prompt. The command refuses to overwrite a file:

```bash
ssh home-core
sudo -u homecompute-openclaw python3 -c '
import getpass, os
key = getpass.getpass("SerpApi API key: ").strip()
if not key:
    raise SystemExit("No key entered.")
os.umask(0o077)
with open("/var/lib/homecompute-openclaw/config/integration-serpapi-token", "x") as f:
    f.write(key + "\n")
print("Saved privately.")
'
```

Provisioning does not deploy the adapter. Qualify one bounded search through
the reviewed release before granting production readiness.

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
verifies the remote adapter path, not installed specialist sessions. SerpApi,
Geoapify, Immich and n8n credentials were not provisioned by this task. Native
activation, provider access and post-release role denials still require the
qualified rollout above.

Focused provider, grant, team, installer, transport and roster checks passed
(76 tests). The repository validator passed its test phase after correcting a
stale existing Lyra research-search assertion, then stopped at an unrelated
compute SSH tunnel guard: the untouched source forwards automation to port
18400 while the validator expects 18300. That mismatch was reproduced on the
pristine base revision. Later validation stages were not reached. No production
rollout was performed.

Maps replacement follow-up: Google Maps transport was removed from the source.
Synthetic checks cover Geoapify geocoding, bounded POI lookup, routing,
ambiguity refusal, credential redaction and isolated Open-Meteo requests.
A live unauthenticated Open-Meteo request returned current Copenhagen data
and the expected timezone. The new provider and existing policy/installer
checks passed (73 focused tests). Geoapify live qualification still requires
its dedicated key; no production
rollout or key copying was performed by this change.
