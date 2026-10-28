# OpenClaw discovery and n8n ownership assessment

Checked 2026-10-09 (Europe/Copenhagen). Infrastructure inspection was read-only:
strict known-host SSH, Docker metadata, SQLite `mode=ro` plus `query_only`, and
health/model-list GETs. Three bounded synthetic model requests were also made
under the existing client credential. No live workflow, device action,
notification, deployment, credential, schedule, or production configuration
was changed. Household payloads, prompts, credential values, and execution
outputs were excluded from the inventory.

## Host placement: the stated two hosts are not the discovered topology

`home-core` is **the GMKtec NucBox_K15**, with Intel Core Ultra 5 125U,
14 logical CPUs, 47,738 MiB RAM, NixOS kernel 6.18.48, and approximately
742 GiB free on its local 937 GiB filesystem. DMI reports `GMKtec`,
`NucBox_K15`, `V1.1`; `lscpu` confirms the CPU. There is no discovered second
K15, and no checked-in M7/Ryzen host configuration. A comparison between
separate M7 and K15 deployments would therefore be fabricated. Confirm the
existence/address/configuration of the M7 before considering a move.

At inspection, home-core had about 34,230 MiB available RAM and low load
(0.31 / 0.19 / 0.17). n8n used about 847 MiB under its 2 GiB/2 CPU limit;
LiteLLM about 847 MiB under 4 GiB; the agents KVM service about 3.1 GiB
resident. This is a quiet instantaneous sample, not a mixed-load capacity
qualification. The agents guest already reserves 4 vCPUs, 16 GiB RAM, and
an 80 GiB disk. Its filesystem had 68 GiB free.

Prefer an opt-in OpenClaw pilot and separately restricted Codex worker on
this existing home-core, using the current NixOS/Compose lifecycle. Keep
the gateway, workflow state, and production mounts out of the agent worker.
Use one coding task at a time initially; do not interpret idle memory as
permission to allocate all remaining capacity. A container provides a
weaker kernel boundary than the existing agents VM; this difference needs
explicit consideration before autonomous untrusted repository execution.

Spark remains inference-only. It had 14,867 MiB available memory and
8,176 MiB swap in use at inspection. Its Plapre primary container was
already restarting; that pre-existing condition was not investigated or
changed. No model swaps or load benchmark were performed.

## Actual local-model and network path

The production path is:

```text
OpenAI-compatible client -> https://ai.home.arpa/v1 -> Caddy -> LiteLLM
automation-moe -> openai/automation-qualification
-> http://172.28.200.1:18005/v1 -> restricted SSH forwarding
-> home-spark 127.0.0.1:18300 -> qwen38-flash-ultrafast
```

The live mounted LiteLLM configuration, not merely the repository example,
maps `automation-moe` to `openai/automation-qualification`. Its selected
environment URL is `http://172.28.200.1:18005/v1`, and its request/stream
timeouts are 600 seconds. The `assistant-canary` alias selects the same
backend with 120-second timeouts.

The **active backend is the UltraFast challenger**, image
`qwen38-flash-dgx:iter6d-20260910`, rather than the previously documented
Flash-Next quality baseline. Container arguments advertise:

| Runtime setting | Observed value |
| --- | --- |
| Served name | `automation-qualification` |
| Maximum model length | 262,144 tokens |
| Maximum sequences | 8 |
| Batched tokens | 8,192 |
| Tool parser | `qwen3_xml` |
| Reasoning parser | `qwen3` |
| Automatic tool choice | Enabled |
| KV cache dtype | `auto` |

`GET /v1/models` at the backend also reports `max_model_len: 262144`.
These are runtime settings and advertised limits, not evidence of usable
long-context accuracy, reliable reasoning, or eight concurrent assistant
sessions. Start a pilot at concurrency one and a conservative context
budget; qualify compaction and multi-step tasks before expanding it.

The private compute interface `enp45s0` is DOWN. The existing restricted
compute SSH tunnel is active. Avoid adding a second LiteLLM or direct
assistant access to the Spark host/Docker socket.

`https://ai.home.arpa/healthz` returned 200 with the existing private CA.
The Mac's existing script credential lists only `automation-moe` and was
used locally without copying or printing it. The documented no-port
Tailscale gateway URL is misleading: Funnel port 443 currently proxies
TTLock on loopback 8085. Caddy publishes Tailscale 8443 and LAN 443.
Despite a matching legacy hostname environment setting, the Mac's test
of `https://home-core.tail479ad.ts.net:8443/healthz` failed the TLS handshake;
an explicit Tailscale-address test timed out. Tailnet gateway connectivity
must be verified from the intended client before promising remote access.
Do not disable certificate validation to conceal this failure.

## Bounded synthetic protocol evidence

Requests used `automation-moe`, temperature zero, `max_tokens: 128`,
`chat_template_kwargs.enable_thinking: false`, one request at a time, and
a 45-second client timeout. Inputs contained only synthetic arithmetic.
No external tool was called: its result was provided by the test client.

| Check | Result | Elapsed seconds |
| --- | --- | --- |
| Scoped model listing | Only `automation-moe` visible | 0.055 |
| Required `multiply` tool, arguments `a=7,b=9` | Correct single tool call | 0.884 |
| Follow-up with tool result `63` | Exact final answer `63` | 0.451 |
| Strict JSON schema, integer total and synthetic source | Exact accepted object | 0.502 |

This establishes a small Chat Completions tool/result/schema exchange through
the production alias. OpenClaw-specific tool semantics, recovery, structured
errors, injection resistance, large context, sustained tasks, and mixed load
are still unverified. These checks do not justify replacing the local model
or enabling consequential tools.

## Live n8n architecture and workflow recommendations

n8n 2.38.3 runs in `homecompute-automation-n8n-1`, with persistent
`/srv/state/automation/n8n/database.sqlite`. Its current database is SQLite,
not the existing PostgreSQL service. Docker restart is `unless-stopped`,
root filesystem read-only, capabilities dropped, 2 GiB RAM and 2 CPUs.
Its state directory is mode 0700; the environment file is root mode 0600.
All home-core containers observed were healthy. Raw database export is
unnecessary for this integration and would include personal information.

The read-only database contained **37 workflows: 21 unarchived and 14
active**. Published versions were read from `workflow_history` using
`activeVersionId`; unactivated workflows were assessed from their drafts.
Node definitions establish available behavior, not successful execution
of every branch. Retained execution metadata includes successful recent
scheduled runs as well as Aula/calendar failures, cancellations, and
waiting executions. Their root causes were not inspected. Historical
counts should not be treated as current failure rates or retried blindly.

Recommendation numbers match the request: **1** stays entirely in n8n;
**2** stays in n8n with a selected read-only assistant capability;
**3** stays in n8n and emits a bounded investigation event on a meaningful
failure/anomaly. **4**, migration, is not currently justified.

| Workflow (exact live name) | Status | Recommendation and rationale |
| --- | --- | --- |
| Aula - Family Briefings | Active | **2**: n8n owns source collection, overlap suppression, delivery, Data Table state/audit, and MQTT projection. Expose a scoped current briefing/status reader, never rerun delivery on assistant query. |
| Aula - Person Week Schedule | Active | **2**: keep the 05:00/17:00 agenda refresh and HA/MQTT publication. Read the resulting agenda; do not create a competing assistant schedule. |
| Aula - Shared Vacation Gate | Active | **1**: deterministic participation/vacation gate shared by Aula workflows. Its refreshed HA projection may inform a briefing, but an assistant must not override the gate. |
| Sub-Workflow: Aula Collector & Analyze | Active | **3**: retain source grounding, deduplication, bounded retry, and summary review. Submit repeated, classified failures to investigation after filtering private payloads. Existing direct local-model calls stay in n8n. |
| Aula calendar sync | Active | **2**: keep event extraction, calendar identity, invitation reconciliation, participation webhook, and synced-history ownership. Expose read-only sync status; creating/updating/removing events continues through existing confirmation logic. |
| Aula workflow | Inactive | **1**: leave the legacy workflow inactive. It still contains delivery and HA paths; do not accidentally reactivate a second scheduler. |
| Family Gmail + Telegram calendar | Active | **2**: keep Gmail polling, parser/dedup, calendar operations, email/Telegram delivery. Expose source-scoped calendar/status reads; changing calendars or sending messages needs explicit action approval. |
| Shopping list | Active | **2**: retain the 07:00 run, webhook paths, offer/category agents, HA shopping/state writes, and store Data Table lookup. Read the existing list/offers projection. Agent exploration must not invoke shopping mutation branches. |
| Shopping nearby stores weekly verification | Active | **1**: Monday 04:00 deterministic verification/cache update belongs in n8n; reuse its stored result. No personal-agent loop is needed. |
| Lager · categorize storage items | Active | **1**: keep the 07:00 categorization, webhooks, structured parser, and HTTP update path. Read results from the owning app if needed; an assistant adds no demonstrated advantage to routine categorization. |
| Battery Plan Adherence Monitor | Active | **3**: keep telemetry evaluation, wait/recheck logic, history, and Telegram delivery. The actual six-field schedule is hourly at minute 7, despite legacy node labels. Emit deduplicated evidence for sustained mismatch, with physical-control tools unavailable. |
| Battery Plan and HomeCompute Weekly Review | Active | **3**: keep Sunday 08:00 battery and 09:00 systems review, snapshot preparation, direct model analysis, and delivery. Hand a verified recurring software issue to investigation; battery control remains outside agents. |
| Notion AI automations | Active | **2**: retain 07:00/21:00 schedules, filters, structured result and Notion updates. Read bounded unresolved-task/status summaries; granting existing workflow execution would also grant writes. |
| Weekday weather and clothing · Home | Active | **1**: 07:15 weekdays, presence/activity checks, forecast and HA notification are deterministic. Keep one scheduler and notification owner. |
| Sub-Workflow: Web Search with Fallback | Inactive | **2**, only if required: wrap bounded Tavily/Brave search with quotas and provenance, keep fallback behavior in n8n. Retrieved text remains untrusted. No activation was performed. |
| Aula LLM comparison worker (internal) | Active | **1**: keep benchmark-only execution, model branches and temporary Data Table results. It is not a personal-assistant capability and can disclose data to cloud models. |
| Aula LLM manual comparison | Inactive | **1**: keep manual-only benchmark/report flow; no assistant invocation. It fetches Aula data and sends Telegram. |
| HomeCompute TEST ONLY - model benchmark lab | Inactive | **1**: keep isolated benchmark webhooks and request validation. Do not expose model/endpoint choice to a general assistant. |
| Local MoE — Danish tool-call qualification | Inactive | **1**: retained qualification tool; a controlled manual test, not an autonomous workflow. |
| TEST ONLY - Aula Gemini comparison 2026-09-28 | Inactive | **1**: leave comparison draft inactive; no migration or exposure. |
| TEST ONLY Aula OpenAI snapshot comparison | Inactive | **1**: leave comparison draft inactive; no migration or exposure. |

The 16 archived workflows were retained test/verification artifacts, not
migration candidates. `automations/update-check/n8n-workflow.json` exists
in the repository but no corresponding live update-check workflow was
discovered. Repository exports are templates/snapshots and must not
overwrite newer live published definitions.

## Integration mechanism and existing exposure

Instance MCP is already enabled (`mcp.access.enabled=true`). Twenty of the
21 unarchived workflows are marked available in MCP, including calendar,
HA state/service, Telegram, Notion, and comparison paths. Connecting an
assistant with the existing administrator identity would therefore widen
access considerably. No existing MCP setting or client was altered.

Prefer a narrowly authenticated n8n HTTP Request handoff for the synthetic
repair event and a dedicated read-only briefing/status webhook for initial
assistant queries. n8n supports Header and JWT authentication; validate
the body, source identity, size, replay/idempotency key, and response schema
in deterministic code. Keep private data out of a coding incident envelope.
[Webhook credentials](https://docs.n8n.io/integrations/builtin/credentials/webhook/)

For tool discovery later, use a dedicated MCP Server Trigger workflow with
only explicit read-only tools. Instance MCP also supports workflow creation,
editing and execution; workflow exposure is shared across clients within
the user's permitted view. Current documentation describes per-client OAuth
permissions, but those exact scopes/tools must be verified against 2.38.3
before use. [Instance MCP](https://docs.n8n.io/connect/connect-to-n8n-mcp-server/),
[MCP Server Trigger](https://docs.n8n.io/integrations/builtin/core-nodes/n8n-nodes-langchain.mcptrigger/)

The public API uses `X-N8N-API-KEY`. Fine-grained API key scopes are an
Enterprise feature; non-Enterprise keys have full account access. Do not
label an ordinary account API key read-only. An adapter can hold such a
credential outside the model runtime and expose an enforced allowlist of
redacted GET operations; adding one is unnecessary until a concrete reader
is selected. [API authentication](https://docs.n8n.io/connect/n8n-api/authentication)

## State and memory ownership

| Information | Authoritative owner | Assistant use |
| --- | --- | --- |
| Preferences and explicit personal facts | One chosen assistant's native persistent memory, within the existing principal/domain contract | Reviewable, bounded, correctable memories; no parallel Hermes/OpenClaw copy by default |
| Conversation context | The assistant session | Retain/compact per session; avoid importing household source archives |
| Project facts and decisions | Repository docs/issues/PRs | Read links and save approved decisions to the project; store only references in assistant context |
| Delivered notifications | Existing n8n state/Data Tables and delivering application | Read status and source references; never infer delivery solely from assistant recollection |
| Workflow execution, retries, dedup | n8n SQLite, workflow static data, Data Tables | Query redacted status; do not duplicate the state machine or use it as an agent scratch database |
| Coding/investigation tasks | One bounded task ledger in the worker/integration layer | Task ID, idempotency key, statuses, attempts, session/commit/PR links and notification acknowledgement |
| Historical observations | HA/application telemetry and n8n run audits | Retrieve selected observations with timestamps and provenance; do not clone raw telemetry into memory |

Observed Data Tables: `Aula Family State` (5 rows), `Aula Family Runs`
(66), `Shopping Nearby Stores` (1), `Aula LLM Comparison Runs` (0).
Calendar sync, Gmail/calendar, collector retries, and battery monitoring
also use workflow static data. n8n's `agents_memory_entries` and
`instance_ai_observational_memory` had zero rows: their presence does not
establish a populated shared assistant memory service.

The running control-plane PostgreSQL belongs to LiteLLM; Immich has its own
PostgreSQL and Valkey. Redis is therefore not a discovered general-purpose
agent task queue. Reusing existing PostgreSQL would require an independently
provisioned database/user and backup contract, not reuse of gateway credentials.
A small isolated task ledger is enough for a single-worker synthetic pilot;
do not build the previously proposed canonical personal-event service simply
to connect coding tasks. Existing `docs/personal-data-and-memory.md` remains
the privacy/ownership baseline for any later personal-data admission.

## Hermes coexistence and outstanding gates

The existing agents KVM service is active. Strict-key SSH via home-core
reached the `agents` Ubuntu guest; an OpenShell `agent-owner` container was
up 17 hours. The existing profile configuration selects `assistant-canary`.
The authenticated dashboard proxy is active. Neither home-core nor the
guest had a Codex executable on the inspected default PATH.

The checked-in configuration explicitly classifies the agents VM
`synthetic-only`, has off-host backups disabled, and permits temporary
maintenance Internet egress. A daily same-host Restic bootstrap timer exists;
it is not off-host disaster recovery. No assistant household capability,
messaging integration or memory sharing was verified in this inspection.

An OpenClaw pilot should coexist with the Hermes canary in separate state,
credentials and scheduling. Select a single personal-assistant owner before
adding real preferences, briefings or proactive jobs to both. Hermes is an
existing alternative that could satisfy part of this request; adding a
second assistant is useful only if OpenClaw's demonstrated orchestration or
interface benefits justify it. No Hermes migration is authorized or needed
for the synthetic coding pilot.

Remaining gates: approved deployment and secret provisioning; independently
authenticated unattended Codex/GitHub worker; tailnet gateway verification;
backup/restore before household data; scoped n8n capability selection;
OpenClaw-specific model qualification; and real notification/draft PR
integration in an explicitly safe test context. Read-only discovery does
not grant the assistant administrator SSH, Docker, database, HA or n8n keys.

## Reproducing safe discovery

These commands disclose metadata only and do not trigger workflows or models:

```bash
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes home-core \
  'lscpu; free -m; df -h /srv; sudo -n cat /sys/class/dmi/id/product_name'
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes home-core \
  'sudo -n docker ps --format "{{.Names}}\t{{.Status}}"; systemctl is-active homecompute-agents-vm.service'
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes home-spark \
  'docker ps --format "{{.Names}}\t{{.Status}}"; curl -sS --max-time 10 http://127.0.0.1:18300/v1/models'
curl --cacert ~/.config/homecompute/home-core-root.crt \
  --max-time 10 https://ai.home.arpa/healthz
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes home-core \
  'sudo -n python3 -' <<'PY'
import json, sqlite3
db = sqlite3.connect('file:/srv/state/automation/n8n/database.sqlite?mode=ro', uri=True)
db.execute('PRAGMA query_only=ON')
for workflow_id, name, active, archived, version, settings in db.execute(
    'SELECT id,name,active,isArchived,activeVersionId,settings FROM workflow_entity ORDER BY name'
):
    print(json.dumps({'id': workflow_id, 'name': name, 'active': bool(active),
                      'archived': bool(archived), 'published_version': version,
                      'mcp': json.loads(settings or '{}').get('availableInMCP')}))
PY
```

Avoid unrestricted `docker inspect`, environment dumps, workflow exports,
credential exports, or execution-data dumps: they may disclose credentials
or personal data. Full workflow inspection can remain on-host, with an
allowlisted metadata projection as used here. The synthetic model checks
read the existing local `HOMECOMPUTE_SCRIPT_API_KEY` in-memory, sent three
small fixed Chat Completions requests, validated exact results, and printed
only booleans/latencies. No LiteLLM administrative credential was used.

## Implemented monitoring foundation, not production activation

`config/system-monitoring.json` is an operator-reviewed allowlist of systems,
expected containers, service kinds, fixed collector implementations, update
sources and evidence TTLs. It prohibits automatic and physical-device actions.
It cannot supply arbitrary commands or SSH destinations. `scripts/observe-homecompute.py`
runs only when explicitly invoked with `--live` or a synthetic `--fixture`.
No timer, n8n schedule, production assistant tool or notification was enabled.

The collector reuses the operator CLI's fixed read-only SSH status source,
then projects allowlisted metadata into a normalized report. That source still
reads general host/container/package metadata locally on the trusted operator;
it must not be installed in an agent sandbox with administrator SSH keys.
Its model-update reader now projects selected counters before sending them
over SSH; raw changes, upstream text, errors and benchmark artifacts stay on
the host. Additional unit probes use only `systemctl show` metadata. Persistent
services require an explicitly loaded active/running state. An inactive
oneshot is successful only when it actually ran and returned success; absence
from `systemctl --failed` is insufficient evidence of health.
Oneshot status describes its last executed result, not whether its timer is
still enabled or ran on schedule. Detecting missed runs needs separate timer
and last-run freshness evidence; it is not implemented by the current unit probe.

Each observation has `system_id`, `check_id`, `stable_key`, health/update
category, status, severity, bounded evidence, observation time, expiry and
review-only action policy. Findings also have an `episode_key` and start time.
`--previous` reads a previous normalized report without changing it. Unchanged
findings generate no change events; recovery resolves an episode and a later
recurrence gets a new episode. Collection gaps and stale evidence cannot
resolve known incidents. Update evidence includes a canonical source-report
timestamp so a later report with the same counts is still distinguishable.

Update recommendations reuse `automations/update-check/watchlist.json` and
the retained report from the existing weekly model/runtime monitor rather
than a duplicate upstream fetch. Container health and update availability are
separate: image tags, running status, or an empty cached package list do not
establish that installed software is current. Spark apt candidates come from
the existing cached inventory; no apt refresh/install is run. Unknown schemas,
missing/future/stale reports and unknown source metadata remain unknown.

HA is registered but its live collector is disabled (`not_provisioned`). A
future read-only metadata broker must expose only an installed version and
approved aggregate update/unavailable counts, never entity states, attributes,
occupancy or user data. Synthetic fixtures verify that unexpected household
payloads are discarded. No HA credentials or household data were connected
to an assistant or sandbox. New transport implementations require reviewed
code; adding approved containers/units to an existing collector requires only
a reviewed registry edit. Corrective capabilities require separate future
authorization and enforcement, with physical-device actions excluded.

Source inspection and bounded read-only collection found an existing path
bug: the systemd updater's `StateDirectory` writes
`/var/lib/homecompute-model-update-check/report.json`, but the notifier and
operator CLI read an obsolete nested path. The **source fix is implemented**
in `modules/nixos/model-update-monitor.nix`, `scripts/homecompute.py` and the
update-check README. Producer, notifier, permission paths and reader now agree.
The existing production notifier is unchanged until an approved deployment.

The operator collection observed seven upstream changes in the existing
2026-10-05 report, zero pin drift/source errors/outperformers, 149 cached Spark
apt candidates, and the already-restarting Plapre primary. These warrant
review; they do not establish available security fixes, a better model, a
root cause, or approval to install/restart anything. No correction was run.

```bash
# Operator workstation, metadata only; no output is stored by the collector.
python3 scripts/observe-homecompute.py --live
# Supply a locally retained normalized report to suppress unchanged episodes.
python3 scripts/observe-homecompute.py --live --previous /private/path/previous-report.json
python3 tests/system-monitoring-test.py
python3 tests/homecompute-cli-test.py
python3 tests/model-update-check-test.py
```

Validation passed 17 monitoring, 11 CLI and 15 update-check tests,
including denied commands/hosts/actions, sensitive-field exclusion, HA metadata
projection, unavailable/stale evidence, stopped-unit handling, episode recovery,
same-count new reports, real shell/jq projection, and shared state-directory
regression coverage. Production monitoring activation, scoped HA collection,
and any corrective actions remain pending.

An additional source-validation blocker was reproduced: the committed
18-source watchlist contained `installed_revision` fields and a
`github_pull_requests` source that the headless checker rejected. Source
validation, default-pin precedence and the restricted HTTP adapter now support
that existing contract. The GitHub queue uses one bounded page and the existing
Dependabot/flake filter; its marker hashes selected metadata without retaining
titles or bodies. An offline regression executes all 18 committed sources
against synthetic responses, with zero source errors. No upstream API request,
update installation or production monitor execution was used for this repair.
