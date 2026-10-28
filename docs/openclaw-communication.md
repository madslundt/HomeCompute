# OpenClaw communication and supervised Telegram activation

Verified 2026-10-09. The operator approved and activated a supervised connection
to [@plantagevej_openclaw_bot](https://t.me/plantagevej_openclaw_bot). The phone
receiver, trusted adapter, bridge-only TLS relay and dedicated n8n delivery
workflow are running. Telegram receipts verify delivery to the selected private
chat, and the operator confirmed receipt on the phone. All 37 existing workflow
records retained their original versions/activation. This paragraph records the
initial activation; the current host placement and update/task feeds are below.

On 2026-10-10, the operator requested server-only operation and automatic restart.
Telegram reception, the adapter/outbox, task feed and update collector run as
`homecompute-openclaw` systemd services on home-core. The Mac LaunchAgents are
disabled. Configuration lives in `/var/lib/homecompute-openclaw/config` and
retained cursor, receipts and sessions in `/var/lib/homecompute-openclaw/communication`.
Directories are 0700 and files 0600. No Mac login or awake session is needed.

On 2026-10-10, the operator authorized and activated real read-only Home Assistant
monitoring through `http://homeassistant.local/` (port 80), reusing n8n's existing
HA credential. `hcOpenClawHA20261010` publishes the fixed authenticated GET route;
38 earlier workflow records retained their versions and activation. The trusted
adapter and existing five-minute observation timer share the private enabled
registry at `/var/lib/homecompute-openclaw/config/system-monitoring.json`. Ordinary
chat includes fresh, bounded household evidence. HA/device writes were unavailable
at that activation; the separately approved proposal controller is described below.

Native OpenClaw remains in the 24 GiB agents VM with an 8 GiB
limit, `Restart=always` VM supervision and its existing native watchdog.
The dedicated host SSH key permits only fixed native chat/health commands in
the guest and reading Spark's maintenance report. It grants no arbitrary shell
or forwarding; the Mac operator key was not copied. n8n remains the sole sender.
Off-host backup/restore remains a separate household rollout requirement.
## Use the working console

On the existing operator Mac, from the HomeCompute checkout:

```bash
python3 scripts/openclaw-chat.py chat --conversation main
python3 scripts/openclaw-chat.py status --conversation main
printf '%s\n' 'Synthetic test: what is 6 times 7?' | python3 scripts/openclaw-chat.py send --conversation arithmetic
```

Enter `/status` for native gateway health and `/quit` to end interactive chat.
Named conversations retain a private UUID under
`~/.local/state/homecompute/openclaw-chat/`; transcript ownership stays in native
OpenClaw. Machine authentication uses the existing strict-known-host SSH key
through `home-core` to `hermes-operator@10.77.20.2`. The native agent is `main`.
Every turn fixes gateway 9123, the managed inference route and a 45-second native
deadline, with no model/delivery override. The external command deadline is 65
seconds. Input uses shell argument quoting, not executable assistant commands.

Only a completed native receipt with the exact session and
`inference/automation-moe` route is returned. Qualified completed workspace,
bounded exec, browser and native MCP receipts are accepted only with the required
completion and identity evidence. Effects remain unsafe to replay. Reroutes,
unverified failures and lost connections durably pause that local conversation.
Do not retry the same turn automatically. Reconcile its native
session before starting another conversation; switching names is not proof that
a prior effect did not occur. `/status` still works while a conversation is paused.

Earlier two-way synthetic recall passed, including a second verification through
the HTTP adapter. The earlier tool quarantine recorded below is historical.
The October 12 capability rollout retains strict receipt validation and moves
critical authority into independent operator services.

## Approved capability rollout, 2026-10-12

Both dedicated model keys were renewed without changing their scopes. OpenClaw
has workspace reads, drafts and edits, memory lookup, the finite protected CLI
helper and an isolated public browser. The root-owned browser plugin permits
navigation to 14 approved public hosts and observation; interaction, forms,
alternate profiles and private URLs are blocked. Its gateway service forwards
one loopback CDP endpoint to the authenticated worker, preserving the native
managed proxy for other traffic. A separate authenticated DNS-only worker serves
those public names. The operator controller binds DNS in the exact approved
gateway namespace, then drops its child to UID/GID 998 with empty groups, zero
capabilities and no new privileges. Identity drift stops the worker. No native
TCP egress grant is required.

Both agents have separate endpoint-bound MCP credentials for bounded system
observations, coding task submit/status/cancel, and HA propose/status/cancel.
The relay exposes only authenticated POST `/mcp` from the agents guest. Neither
credential can approve, publish or execute. The native Python SDK's newer initial
protocol offer negotiates the supported June 2025 version; later requests retain
strict protocol checks.

All three reviewed coding repositories allow automatic isolated preparation.
Publication remains bound to a separately reviewed result digest. A real worker
qualification task (`351ac753-4e28-4b83-9cff-4ee912c972a2`) produced two proposed
files, passed the controller's fixed checks and stopped in `review`. It was not
published. The schema permission failure from the preceding test is retained
as a failed task; schema readability is now checked under the real isolated UID.

The independent HA controller accepts bounded light, climate and cover proposals.
A root operator reviews fresh target state, approves its exact digest and executes
within five minutes against unchanged preconditions. Approval is consumed before
the effect; uncertainty cannot be retried. Native agents have no approval or
execution interface. Provisioning preserved 39 existing workflows and 20 existing
credentials. Both agents passed native proposal/status/cancel checks. An operator
also reviewed a real light against a fresh state snapshot and cancelled the
proposal. No physical action was executed.

Retained runtime/forward recovery remains supervised. Destructive native `Error`
replacement requires operator handling because the old quarantine-only restore
validator intentionally refuses the expanded capability profile. The approved
replacement now has a Ready native sandbox and restored conversations and
databases. The final request through home-core passed five actual model tool
calls: system inventory, host status, protected calculation, public navigation
and a page snapshot. Native browser checks also blocked private and unapproved
hosts and interactions after removing the temporary public REST rule. Supervision
and the retained Telegram receiver are active, with boot dependencies verified.
The final full-state archive and protected operator recovery bundle have verified
root-only copies on both the guest and home-core. All three online SQLite backups
passed integrity checks without changing live WAL/SHM ownership. Current status
and archive hashes are recorded in the validation JSON.

The encrypted NAS archive-backup module is prepared and disabled. It requires a
separate NAS share and backup account; the Immich library is not a backup destination.
A temporary local Restic repository passed an encrypted snapshot and restore test,
and enabled/disabled Nix module evaluation passed. No NAS repository was written.
The backup scope is completed recovery archives on home-core, rather than continuous
live VM or agent-state capture. Keep an exported recovery key separately from both
home-core and the NAS before relying on it for disaster recovery. Native OpenClaw
activation and browser qualification status are recorded in the validation JSON.

## Selected phone chat: dedicated Telegram bot

The selected user experience is a separate OpenClaw contact in Telegram: press
Start once, then write ordinary messages without an `/openclaw` prefix. `/status`
checks known transport/observation state. `/start` and `/help` queue a deterministic
welcome without calling the model. Replies and proactive notifications use the
same private audience through n8n and a **new dedicated bot credential**.

Create the bot in [BotFather](https://t.me/BotFather) with `/newbot`, choose its
name and a username ending in `bot`, then open that bot and press Start. Telegram's
[official creation instructions](https://core.telegram.org/bots/features#creating-a-new-bot)
describe this step. Keep the generated token in an operator-owned private file
outside this checkout and the assistant sandbox; do not paste it into chat.
Creating the bot and granting a transport credential are operator prerequisites;
no existing household Telegram credential is selected for this dedicated route.
The following command prompts locally with hidden input and stores the token
with mode 0600. It performs no Telegram/network call and refuses to overwrite an
existing file. Run it yourself in Terminal after creating the bot:

```bash
python3 scripts/openclaw-telegram.py store-token \
  --tokens ~/.config/homecompute/openclaw/telegram-tokens.json
```

`scripts/openclaw-telegram.py` is the trusted receiver on home-core alongside
the adapter. It uses fixed HTTPS Telegram long polling and fixed host loopback
`http://127.0.0.1:18793/conversation`; it opens no public port. It verifies `getMe`
against the pinned bot ID and refuses any existing webhook. It has no send,
webhook-removal or action API. n8n continues to own outgoing messages. The old
shared-bot `/openclaw` subworkflow remains an inactive alternative.

After the original secret/activation approval, place `bot_token` in a mode-0600
JSON file. This file may initially contain only that key. Inspection prints the
bot ID/username and candidate chat/human IDs from `/start`, without message text,
tokens, enrollment or update acknowledgement:

```bash
python3 scripts/openclaw-telegram.py inspect --tokens /private/path/telegram-tokens.json
```

Explicitly verify the candidate is your own Telegram account and private chat.
Copy `config/openclaw-telegram.example.json` outside the checkout and replace its
bot/chat/user placeholders. Its destination must match the adapter and n8n
delivery configuration. Add `conversation_token` to the private token file,
using only the adapter's dedicated conversation credential. Provision the same
new bot token as a separate n8n Telegram credential for the outbound workflow.
The prepared outbound workflow includes a disabled 10-second delivery schedule;
enable/publish it only after manual receipt qualification and activation approval.
It claims one queued reply per run; overlapping runs cannot claim a second event
while a delivery is in flight. With the schedule off, outgoing delivery is manual.
Do not enable a Telegram Trigger for this bot: this receiver owns its cursor.

```bash
python3 scripts/openclaw-telegram.py run \
  --config /private/path/telegram.json \
  --tokens /private/path/telegram-tokens.json \
  --state /private/path/telegram-state
```

Only the exact selected human in the selected private chat is admitted; groups,
other senders, bots, attachments and empty/oversized text are discarded. A private
durable cursor pins all identities and uses `telegram:<bot-id>:<update-id>` for
replay. It advances after adapter admission, so a lost response retries only the
same durable request after operator restart. An uncertain/running turn durably
pauses the receiver. Reconcile the adapter/native turn before clearing its private
cursor's `paused` flag; preserve the offset and identity, and never reset the
state to retry. Read-only polling retries transient network/429/server errors
up to five attempts with a fixed cursor and bounded backoff; authentication or
competing-poller errors stop immediately. Adapter submissions are not retried
automatically. Telegram retains undelivered updates for at most 24 hours, so this
path does not guarantee recovery after a longer outage.
[Telegram polling semantics](https://core.telegram.org/bots/api#getupdates).

The receiver is boundary-tested and connected for the approved supervised test.
Actual phone updates and Telegram delivery receipts have been verified. The
receiver has systemd supervision with a durable admission guard. A normal crash
restarts it; an uncertain admission intentionally pauses until reconciliation.

## Activated placement and reconciliation

`scripts/setup-openclaw-core-communication.py --install` stages boot-persistent
services without starting them. `--activate-services` starts the status snapshot,
adapter, feeds and timers; `--activate-telegram` verifies the retained unpaused cursor
and absent admission guard before starting reception. Preserve state and inspect
failures before clearing a pause; never reset native sessions or polling offsets.
The update timer runs every five minutes using local core metadata and a fixed
read-only Spark report fetch. A privileged pre-step projects only the finite model
summary from its existing private source; the adapter remains unprivileged.

The core relay sources and seven-day certificate/key are under
`/srv/state/openclaw-communication/`. The certificate chains to the existing
Caddy CA; its signing key never left home-core. The relay admits only n8n's
`172.28.201.2` source on `172.28.201.1:19443`. An exact temporary INPUT exception
admits only `br-hc-n8n`, that source/destination, TCP 19443 and NEW/ESTABLISHED
connections, with comment `hc-openclaw-communication-supervised`. No LAN/public
listener or general bridge exception was added. TLS, wrong-role, missing-auth,
unknown-path and non-n8n source denial were verified before phone delivery.

The new n8n workflow is `hcOpenClawTelegram20261009`, with a 10-second delivery
schedule and two new encrypted credentials: `hcOpenClawTelegramBot20261009` and
`hcOpenClawDelivery20261009`. It belongs to the existing personal project. Only
n8n was recreated to add the private hostname mapping; the original image,
environment, read-only policy and HA mapping were preserved. Pre-activation
online SQLite backup, original workflow metadata, import/publish diagnostics and
the exact temporary firewall rule are private under
`/srv/backups/openclaw-communication-activation/`.

A prior non-replayable `memory_search` receipt was reconciled without a model
retry; its final answer and provenance remain in transport state.

For this supervised canary, the original native deny list was preserved and
`session_status`, `memory_search`, `memory_get` and `write` were additionally denied.
All four previously allowed tools are therefore unavailable; chat retains its
native conversation context and `/status` uses the adapter's deterministic path.
No additional tool, broker or production authority was granted, and the receipt
replay guard remains unchanged. A new tool-free native reply passed verification
and real Telegram delivery after reconciliation. The previous native deny list
is backed up privately in `native-tools-deny-before-phone.json`; restore it only
after stopping this supervised connection and reviewing the native replay issue.

The 2026-10-10 HA qualification exposed a pinned compaction failure when an
explicit allowlist resolves to no callable tools. The live supervised canary now
uses `allow:[]` plus `deny:[...,"*"]` at both global and main-agent scope. Empty
allow alone is permissive in this release; both wildcard denies are required.
Pure tests against the installed matcher/guard confirmed denial of all tested and
future tool names without the construction failure. The native config and
`.last-good` mirror passed config validation, and the existing gateway lifecycle
restarted it with no sandbox rebuild, VM reboot or conversation reset.
`restore-openclaw-nemoclaw.py` now accepts this strict policy and preserves it during
native Error recovery. Stable agent database, sessions and workspace files were
preserved; SQLite's transient shared-memory file changed during restart as expected.
The failed isolated validation conversation remains uncertain and was not replayed;
phone conversations and outbox receipts were unaffected.

## Trusted communication adapter

`scripts/openclaw-communication.py` is a stdlib, loopback-only service on 18793.
It runs outside the assistant on home-core and reuses the console transport.
`HOMECOMPUTE_OPENCLAW_TRANSPORT=home-core` selects direct, pinned guest SSH using
its dedicated host identity and the root-owned restricted command helper.

Prepare a private directory and a copy of
`config/openclaw-communication.example.json` after selecting the destination.
The placeholder fails validation. `destination` is one fixed
logical audience, `conversations` is a finite principal list, and `projects` and
`actions` default to empty. Action entries map a reviewed finite action ID to its
exact target. Empty allowlists deny task/action notification admission.
Dedicated transport tokens are a private JSON object containing three distinct
values of at least 32 characters: `collector`, `conversation`, `delivery`.
Provisioning them requires the original explicit secret approval. They are
transport credentials, never broker operator/worker/publisher credentials.

```bash
python3 scripts/openclaw-communication.py \
  --config /private/path/communication.json \
  --tokens /private/path/transport-tokens.json \
  --state /private/path/communication-state
```

The service supports only authenticated POST operations:

| Path | Role | Bounded operation |
| --- | --- | --- |
| `/conversation` | conversation | Fixed audience/principal, stable request ID, text <=4,000 characters; same-session native turn |
| `/status` | conversation | Outbox state, fresh/stale incident metadata, known task/action snapshots; no model or production action |
| `/observations` | collector | Existing normalized read-only report, registry checks and their reviewed TTLs |
| `/analysis` | collector | Explain one stored, fresh, unresolved incident in an isolated native session |
| `/tasks` | collector | Up to 100 allowlisted public task snapshots including authoritative `updated` |
| `/actions` | collector | Up to 100 finite action/target snapshots including authoritative `updated` |
| `/claim` | delivery | Atomically claim one notification for the selected audience |
| `/ack` | delivery | Record the matching claim and verified delivery receipt |

Task and action snapshots must come from their owning broker. Poll explicitly
tracked IDs or use a separately qualified bounded event feed. The existing
100-row recent list is not a lossless event cursor. This adapter adds no broker
approval, publishing, execution, arbitrary URL/command, HA or n8n administrator
capability. `/approve`, `/publish` and `/execute` conversation commands return
operator instructions. Arbitrary prose remains untrusted assistant input and
cannot gain broker authority.

The observation path reuses the registry and collector in
`scripts/observe-homecompute.py`. A trusted operator collector sends only its
normalized report to `/observations`; collection does not call the model or send
a message. The authorized HA route is active on home-core. Extra fields are dropped and unknown evidence
keys rejected; assistant context receives finite identifiers, enumerated
health/update evidence, provenance timestamps and `monitor:<episode_key>`.
Registry TTLs and source-report expiry are enforced. Missing, stale and unknown
evidence cannot announce recovery. `/analysis` returns its explanation to the
authenticated caller; it does not broadcast a second proactive report. Native
analysis passed with an explicitly synthetic fixture and a safe receipt.

## Delivery behavior and ownership

### Connected machine reads

The core adapter enables `--infrastructure-registry PATH --core-host-mode`.
Ordinary chat receives bounded host evidence; broker tools are unnecessary for
these reads. A root snapshot runs every 30 seconds; cached health older than 60
seconds is unknown. Spark uses the fixed restricted snapshot command. `/systems`
lists capabilities, `/health all` gives a direct report, and `/investigate SYSTEM`
interprets evidence. Metrics describe the hosts; update reports retain source times.
The finite targets are `home-core`, `home-spark`, `home-assistant`, and `all`.
Queries never populate the proactive observation outbox. Reply delivery retains
the existing n8n owner, selected private audience and durable request receipts.
The operator-only projection can be checked without Telegram:

```bash
python3 scripts/openclaw_infrastructure.py home-core --core-host-mode
python3 scripts/openclaw_infrastructure.py home-spark --core-host-mode --prompt
```

The sandbox gains no SSH key, HA token, shell, Docker, n8n admin or native tool.
The pinned OpenClaw 2026.9.1 implementation does not classify ordinary custom
plugin tools as replay-safe even with `toolMetadata.replaySafe:true`. Native
tool denials remain in force; the console still rejects unsafe receipts and
does not retry uncertain turns. Tests cover admission deduplication, collection
deadlines, metadata exclusion, denied targets/arguments, and unsafe native receipts.
Each SSH subprocess is capped at four seconds; parallel collection takes at
most eight seconds before the existing 65-second native deadline. Slow reads
return unknown. Health and advisory update availability remain separate.

The source `automations/agent-investigation/n8n-ha-metadata-workflow.json` remains
inactive with its gate closed. `scripts/setup-openclaw-home-assistant.py` validates
the fixed graph, snapshots the existing n8n database, creates only a dedicated
Header Auth reader credential, binds the existing HA credential and publishes
`hcOpenClawHA20261010`. It disables success/error/manual payload retention and
MCP exposure. Its native HA nodes perform only GET `/api/config` and `/api/states`,
as documented by the [HA REST API](https://developers.home-assistant.io/docs/api/rest/).
The live route rejects unauthenticated requests and all input selectors. n8n
retains the production HA token; only its finite webhook bearer is available to
the trusted host reader, mode 0600 outside Git and the assistant VM.

The schema-2 projection is capped at 64 KiB. It monitors availability, battery
optimizer and telemetry health, whole-house grid meter, tariff, operation plan,
physical grid availability, and update entities for core, supervisor, OS,
house_battery, Strømligning and Watts Live. Collection/source gaps become explicit
incidents, without claiming an HA crash or fabricated recovery. Every five minutes
the existing feed admits HA health/update checks, alongside compute update checks,
into the unchanged outbox. HA observations expire after 15 minutes. Current
unknown/stale entity values never enter numerical analysis; unchanged findings
stay quiet and incidents/recoveries share the existing per-system grouped claims.

A reusable bounded read exposes 22 verified numerical observations, up to 200
VAT-inclusive price slots and 32 modeled plan blocks. Whole-house import/export
comes from Watts Live; connected load and device charge/discharge are separate.
Integrated battery energy and savings are labeled estimates, configured settings
are observations only, and the operation plan does not prove execution. Numeric
fluctuations do not enter notification state. `sampled_at` denotes the selected
integration refresh/meter heartbeat; `reported_at` retains the entity's state-change
time. Stable settings/update states are collected as current HA states; they do
not prove a recent device report or a fresh upstream update check. Solar has no
verified source and today’s estimated realized savings currently reports unknown.
Battery/HA-only questions select HA evidence; cross-system and general questions
retain the compute coverage. Normal conversation gets a compact projection with timestamps and selected future
plan/price intervals, within the native input budget.

From home-core, without the Mac or Telegram:

```bash
sudo -u homecompute-openclaw env HOMECOMPUTE_OPENCLAW_TRANSPORT=home-core \
  /run/current-system/sw/bin/python3 \
  /opt/homecompute/openclaw-communication/scripts/openclaw_infrastructure.py home-assistant \
  --registry /var/lib/homecompute-openclaw/config/system-monitoring.json \
  --ha-transport /var/lib/homecompute-openclaw/config/home-assistant.json --core-host-mode
```

This returns finite operational checks and the reusable energy projection; it
never submits an outbox event or applies a control. The deployed private registry
survives code installation; the Git template remains closed for unprovisioned
systems. The adapter's `--registry` and `--infrastructure-registry` must select the
same private policy. The feeder and adapter use the same private HA transport.

Real bounded reads, a native read-only answer, denied credentials/input selectors,
existing-session/cursor/outbox preservation and successful scheduled admission were
qualified. No synthetic Telegram message was sent. Persistent target dependencies,
service restart behavior and n8n restart ownership are verified. `is-enabled` reports
`disabled` for NixOS attached units, while the effective boot target `Wants` graph
includes all three services and both timers; the root-owned links reside in durable
`/etc/systemd/system.attached`. This is boot configuration proof, not a claim that
an actual production host reboot was performed during this qualification.

Rollback stops only the HA extension, unpublishes `hcOpenClawHA20261010`, removes
its private HA arguments and disables HA in the private registry. Revoke only
`hcOpenClawHAReader20261010` after readers stop. Preserve polling offsets, sessions,
outbox receipts, the existing HA credential and all household workflows. The
pre-import backup is `/srv/backups/openclaw-ha-20261010T094327Z`; do not overwrite
subsequent household state by restoring that database as routine rollback.

Control remains in the existing broker/operator/executor lanes. Production
executors are disabled/unprovisioned and HA/device actions are unavailable;
neither a successful read nor chat text supplies execution approval. This adds
read-only monitoring and boot supervision, without repair authority. Rollback removes
the adapter's two opt-in arguments and unpublishes only the new HA workflow,
revoking its dedicated reader credential. Preserve existing workflow state,
native sessions and outbox receipts. Off-host backup qualification remains separate.
Existing n8n retains Telegram sending and workflow execution ownership. The
small private SQLite transport outbox owns admission/replay receipts and pending
transport envelopes; it is not another personal-memory or task ledger. Native
OpenClaw owns conversation and memory. Existing n8n family/battery workflow state
is unchanged, and reports do not copy their private execution histories.

The outbox emits changed actionable incidents, verified recovery, task/action
approval requests and terminal outcomes. Repeated heartbeats and unchanged
findings are quiet. Quiet hours are 22:00–07:00 Europe/Copenhagen, with a
30-minute incident cooldown. Explicit conversation replies bypass proactive
quiet hours. Deferred incidents are refreshed by unchanged fresh observations;
changed pending evidence is coalesced. An undelivered incident that recovers
stays quiet. A recurrence supersedes a deferred old recovery. Recovery delivery
requires a current fresh healthy observation. Equal-time conflicting snapshots
are rejected and older snapshots cannot move state backward.

Eligible monitoring checks are combined into one readable summary per machine
when the delivery owner claims them. Package/image updates, coverage gaps,
reboot requirements and individual recoveries share one approval reminder.
Each member keeps its original topic, episode, freshness and cooldown. One
durable shared claim binds the members; one verified Telegram receipt
acknowledges exactly that set, including after process loss. Later arrivals
remain pending. Conversation replies and task/action messages retain their
existing delivery behavior. Summaries fit the 4,000-character transport budget;
any excess checks remain pending for a subsequent summary.

Model requests have durable request IDs. Completed responses and their pending
reply envelopes commit together. There is one in-flight model turn and one
delivery claim. A process loss or expired send lease moves the outcome to
`uncertain`; there is no automatic resend. Telegram can send successfully while
its receipt is lost, so exactly-once external delivery is not claimed. Reconcile
the n8n execution and recipient message first, then acknowledge the original
claim with the real message ID if delivery is proven. Never delete the database
to obtain a retry. A duplicate identical acknowledgement is a no-op.

Receipts are retained, with a 10,000-event and 10,000-turn admission cap and
128 MiB SQLite page cap (WAL adds bounded checkpoint overhead). Capacity exhaustion
fails admission rather than forgetting deduplication history. Production state
needs an operator-owned backup/retention procedure before household use.

## Concrete activation and rollback proposal

1. The channel choice is the dedicated OpenClaw Telegram bot. Pin its bot, private
   chat and human sender using the phone setup above. Earlier discovery found
   a private notification audience used by the battery/HomeCompute weekly reports
   and a distinct family audience. This task printed only their fingerprints.
   The shared bot has a disabled inbound trigger; leave its ingress ownership
   intact. Qualify that the new dedicated bot has no webhook or other poller.
2. Approve a **synthetic-only** delivery qualification, its dedicated transport
   secrets and trusted adapter placement. Retain native gateway/pairing auth.
   Keep the adapter on Mac loopback. The complete private route proposal under
   `deploy/openclaw/communication/` uses an operator SSH reverse-forward to
   **home-core loopback** 18793 and a pinned, restricted Caddy relay on the
   existing n8n bridge gateway **172.28.201.1:19443**. Live discovery verified
   n8n's source address **172.28.201.2**; the listener admits that source only,
   and only the finite POST routes. The Caddy relay is a separate trusted
   container with host networking, dropped capabilities, a read-only filesystem,
   0.25 CPU/128 MiB, and `restart=always`. It mounts only its dedicated cert/key
   and has no model/broker/admin secrets. Its optional Compose profile is off.
   The pinned Caddy binary's unused low-port file capability prevents execution
   under an empty capability bounding set; startup copies it into private tmpfs
   without extended attributes. The copy runs unprivileged on the fixed high
   port with all capabilities still dropped and `no-new-privileges` retained.
   Review host-network placement explicitly; it does not grant the assistant
   host networking. Provision a dedicated certificate for the communication
   hostname signed by the existing private CA (the CA key stays outside this
   relay and assistant). The n8n route overlay adds only the hostname-to-bridge
   mapping and uses n8n's existing CA trust. Its application recreates n8n and
   requires approval; preserve the existing HA mapping and published workflows.
   The resulting staged origin is
   `https://openclaw-communication.home.arpa:19443`, not a working live endpoint.
   Certificate trust, source restriction, reverse-forward lifetime and exact
   namespace hop must pass auth and route-denial checks before workflow enabling.
   No private listener or tunnel from this proposal was started.
3. Import `automations/agent-investigation/n8n-notification-workflow.json` as a
   **new inactive workflow**. Bind the new dedicated Telegram bot credential and
   the dedicated delivery bearer-header credential. Set the selected audience
   and chat in its configuration node, then set `configured:true`. Keep retries
   off on sending nodes. The workflow validates destination and Telegram's chat
   and message receipt before acknowledging the outbox. Its default fails closed.
4. For the selected dedicated bot, start the private receiver only after adapter
   and outbound qualification. Pin the selected private chat and human sender;
   ordinary messages enter the fixed loopback adapter. The alternate shared-bot
   `n8n-conversation-workflow.json` can remain inactive. If explicitly selected
   later, import it as a new inactive subworkflow. Pin
   the selected private chat and human sender, and bind only the conversation
   transport credential. The one authenticated existing Telegram ingress owner
   can route `/openclaw ...` updates into it using `telegram:<update_id>`.
   Do not activate a competing Telegram trigger or steal its webhook/polling
   cursor. If no ingress owner can be qualified, continue using the SSH console
   until an explicitly approved private polling/pairing path is implemented.
   Replies enter the same outbox rather than sending from the inbound branch.
5. Using the selected authorized destination, manually verify one synthetic
   conversation, duplicate-update replay, incident, recovery, approval request
   and delivery receipt. Verify no message reaches the family/another chat,
   no chat text approves an action, and no physical or production action runs.
   Then review and enable the one prepared n8n-owned 10-second delivery schedule
   for this supervised phone qualification. It is disabled by default; collection
   scheduling remains separate. Neither template opens a public webhook.
6. Production observation schedules and wider household rollout still require
   off-host backup/restore qualification. Supported native supervision and reboot
   recovery were accepted on 2026-10-10. The failed external-owner path remains
   retired with its two units disabled, and Hermes stays independent. The canary model
   key expires 2026-10-16 at 16:15:04 UTC (18:15:04 Copenhagen); renewal is an
   operator action outside the assistant.

Rollback: unpublish only `hcOpenClawTelegram20261009` with n8n's
`unpublish:workflow --id=hcOpenClawTelegram20261009`. It requires an n8n restart;
recreate only n8n with its original two Compose files and
`/etc/homecompute/automation-tunnel.env` to restore the prior hostname mappings.
The original files are under
`/srv/homecompute/releases/automation-tunnel-20260926/deploy/automation/`.
Stop the home-core receiver/adapter/feeds and the separate communication relay, remove
only the exact temporary INPUT rule recorded in the private backup, and revoke
only the new communication credentials/bot as appropriate. Restore the original
native deny list from the private operator backup after stopping communication.
Do not restore the entire pre-activation n8n database over subsequent household
changes; the backup is a recovery artifact, not the normal rollback mechanism.

Preserve the private polling cursor, outbox and native sessions; reconcile
`sending`/`uncertain` receipts. The rollback does not change Hermes gateway 8080,
shared Docker, the shared Telegram credential or household workflow state.

The relay reaches host loopback 18793 directly; both Mac tunnels are disabled.
The private TLS relay, restricted n8n listener, credentials and destination are unchanged.

## Evidence

`docs/openclaw-communication-validation.json` records three verified native
turns through the real HTTP→SSH→NemoClaw path: two-way recall, duplicate-request
suppression and scoped synthetic incident analysis. Those initial outbox
acknowledgements were mock receipts. The later approved activation records real
Telegram receipts and native-turn reconciliation separately. Twenty-seven transport, eighteen
phone ingress and eleven console tests pass, plus five n8n routing/receipt tests.
The exact current
n8n image also executed the notification workflow in a disposable, network-none,
read-only container with dropped capabilities, 0.5 CPU and 768 MiB memory.
The external Telegram sender and adapter HTTP endpoint were synthetic surrogates
in that engine check; claim/validation/ack workflow nodes ran in real n8n.

```bash
python3 tests/openclaw-chat-test.py
python3 tests/openclaw-communication-test.py
python3 tests/openclaw-telegram-test.py
node --test tests/openclaw-n8n-communication.test.mjs
# Explicit opt-in live synthetic checks; no production sending credentials.
python3 scripts/verify-openclaw-communication.py --live
python3 tests/openclaw-n8n-engine-test.py --live
```

The inherited AGENTS security-scan command was attempted without permitting a
package installation; npm reported `ENOTCACHED` for `@Codex-flow/cli@latest`.
The dependency-free implementation received a separate code/security review and
boundary tests. An unavailable external scanner is not recorded as a passed scan.
Actual Telegram receipt delivery and private HTTPS ingress are now qualified for
the supervised test. Native browser pairing, unattended supervision and
production monitoring remain unqualified.

## Coding handoff and status feed, connected supervised test

`scripts/openclaw_tasks.py` reuses the existing broker and transport outbox.
Native tools remain denied because their pinned replay metadata is unsuitable.
The adapter's explicit `/code PROJECT STABLE_KEY SUMMARY` command submits one
pending investigation without a model turn; it never approves execution.
Identical project/key/content returns the same broker ID, while conflicting
content is rejected. `/cancel-task UUID` checks the reviewed project before
cancellation. `/task UUID` returns the exact latest collected snapshot, including
progress, test outcome/file count/session ID and a validated PR link if present.
This snapshot is not an on-demand liveness probe. `/status` remains bounded.

Activation requires the reviewed authoritative project policy, separate assistant
and read-only snapshot broker tokens, and a qualified private broker. Keep those
tokens on the trusted operator side, never in OpenClaw. Copy
`config/openclaw-task-transport.example.json` into the operator's private config
directory, substitute absolute private paths and set mode0600. Its role-token
file has exactly `assistant` and `snapshot`; its fixed broker origin is loopback
18792, reached directly on home-core without a workstation tunnel.
Validate communication `projects` against the authoritative broker policy, then
add `--task-transport PRIVATE_CONFIG` to the existing adapter command, preserving
all infrastructure options. This task transport is active for `homecompute`.

The same module's CLI feeds authenticated `/task-events` snapshots to the existing
adapter `/tasks` endpoint, with a private single-owner cursor file. The configured
projects select the only admitted tasks. Start it with:

```bash
python3 scripts/openclaw_tasks.py --transport PRIVATE_TASK_CONFIG \
  --communication-config PRIVATE_COMMUNICATION_CONFIG \
  --transport-tokens PRIVATE_TRANSPORT_TOKENS --cursor NEW_PRIVATE_CURSOR --follow
```

Ordered event admission occurs before cursor fsync. Repeating a batch after a lost
admission receipt is deduplicated by the existing outbox. Historical events lacking
snapshots stop the feed for reconciliation; do not reset the cursor to conceal a
gap. Unchanged heartbeats stay quiet, changed phases notify, and deferred progress
is coalesced. Approval/terminal outcomes use the existing n8n Telegram owner and
quiet hours. Coding completion at `review` still requires publication approval.
The 100-row recent list is not a lossless substitute for the cursor feed.

Five synthetic HTTP integration tests cover duplicate submission, progress/result
retrieval, permission denials, cursor gaps, lost admission receipts and duplicate
cancellation. The authenticated canary produced a regression; its failed budget-check
snapshot and recovered 26/26 validation are recorded in the worker receipt.
Rollback: stop this feeder, restart the adapter without `--task-transport` while
preserving infrastructure options, and retain cursor/outbox/broker evidence.
Never remove existing receipts or clear a paused phone turn to obtain a retry.
