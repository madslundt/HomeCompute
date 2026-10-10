# OpenClaw communication and supervised Telegram activation

Verified 2026-10-09. The operator approved and activated a supervised connection
to [@plantagevej_openclaw_bot](https://t.me/plantagevej_openclaw_bot). The phone
receiver, Mac adapter/tunnel, bridge-only TLS relay and dedicated n8n delivery
workflow are running. Telegram receipts verify delivery to the selected private
chat, and the operator confirmed receipt on the phone. All 37 existing workflow
records retained their original versions/activation. Hermes and existing core
healthchecked services remained healthy; no Docker daemon or supervisor change
was made. Production observation/task/action feeds remain disconnected.

This is a supervised canary connection: keep the operator Mac awake. There is no
boot/reconnect supervision, and the temporary firewall exception disappears on
firewall reload/reboot. Unattended household use remains gated by the canary boot
and backup/restore acceptance below. The supervised canary currently answers
from conversation context with tool use temporarily denied, as explained below.

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
`inference/automation-moe` route is returned. Write receipts, reroutes, native
replay warnings, command failure and lost connections durably pause that local
conversation. Do not retry the same turn automatically. Reconcile its native
session before starting another conversation; switching names is not proof that
a prior effect did not occur. `/status` still works while a conversation is paused.

Earlier two-way synthetic recall passed, including a second verification through
the HTTP adapter. The supervised phone canary now denies all four previously
allowed tools, including memory writes/search, after the replay issue described
below. Conversation context remains available. The console still rejects unsafe
receipts and grants no additional native tools or production control.

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

`scripts/openclaw-telegram.py` is the trusted receiver on the same operator Mac as
the adapter. It uses fixed HTTPS Telegram long polling and fixed Mac loopback
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
manually supervised path does not guarantee recovery after a longer outage.
[Telegram polling semantics](https://core.telegram.org/bots/api#getupdates).

The receiver is boundary-tested and connected for the approved supervised test.
Actual phone updates and Telegram delivery receipts have been verified. Mac
sleep and the existing canary boot blocker still limit availability.

## Activated placement and reconciliation

Private configuration and credentials are under
`/Users/madslundt/.config/homecompute/openclaw/` (directory 0700, files 0600).
Transport state, receiver cursor, PID records and private logs are under
`/Users/madslundt/.local/state/homecompute/openclaw-communication/`. The three
Mac processes are manually started, detached processes, not login/boot services.
Preserve state and investigate a dead process before restarting or clearing a
pause; do not reset native sessions or polling offsets.

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

One native phone turn completed with two successful `memory_search` calls, but
OpenClaw marked them `replaySafe:false`. The console correctly paused instead of
retrying. Operator reconciliation verified native success/stop, no write,
messaging or cron effects, and empty pending-input/delivery queues. The existing
final answer was recovered once into its original outbox request; the model turn
was not rerun. Its private provenance receipt is retained beside transport state.

For this supervised canary, the original native deny list was preserved and
`session_status`, `memory_search`, `memory_get` and `write` were additionally denied.
All four previously allowed tools are therefore unavailable; chat retains its
native conversation context and `/status` uses the adapter's deterministic path.
No additional tool, broker or production authority was granted, and the receipt
replay guard remains unchanged. A new tool-free native reply passed verification
and real Telegram delivery after reconciliation. The previous native deny list
is backed up privately in `native-tools-deny-before-phone.json`; restore it only
after stopping this supervised connection and reviewing the native replay issue.

## Trusted communication adapter

`scripts/openclaw-communication.py` is a stdlib, loopback-only service on 18793.
It runs outside the assistant and reuses the console transport. Its current
qualified SSH placement is the operator Mac. Running unattended on home-core
would require a separately reviewed operator identity and transport qualification;
the Mac key must not be copied into a sandbox or silently reused on another host.

Prepare a private directory and a copy of
`config/openclaw-communication.example.json` after selecting the destination.
The placeholder intentionally fails validation. `destination` is one fixed
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
a message. HA remains disabled. Extra fields are dropped and unknown evidence
keys rejected; assistant context receives finite identifiers, enumerated
health/update evidence, provenance timestamps and `monitor:<episode_key>`.
Registry TTLs and source-report expiry are enforced. Missing, stale and unknown
evidence cannot announce recovery. `/analysis` returns its explanation to the
authenticated caller; it does not broadcast a second proactive report. Native
analysis passed with an explicitly synthetic fixture and a safe receipt.

## Delivery behavior and ownership

### Prepared on-demand infrastructure reads

`scripts/openclaw_infrastructure.py` reuses the observation registry and collector.
The trusted Mac adapter can opt in with `--infrastructure-registry PATH`; its
default remains disconnected. `/systems` lists configured capabilities, `/health
home-core` returns fresh metadata without a model call, and `/investigate
home-spark` supplies fresh bounded evidence to the existing native conversation.
The finite targets are `home-core`, `home-spark`, `home-assistant`, and `all`.
Queries never populate the proactive observation outbox. Reply delivery retains
the existing n8n owner, selected private audience and durable request receipts.
The operator-only projection can be checked without Telegram:

```bash
python3 scripts/openclaw_infrastructure.py home-core
python3 scripts/openclaw_infrastructure.py home-spark --prompt
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

The new inactive `automations/agent-investigation/n8n-ha-metadata-workflow.json`
owns the HA credential and exposes only authenticated input-free GET
`/webhook/homecompute-openclaw-ha-metadata` on existing core-loopback n8n port
15678. Its two native HA nodes read `/api/config` and `/api/states`, then project
only version, aggregate update count, aggregate unknown/unavailable count and
source timestamp. Entity IDs, states, attributes, personal payloads and credentials
stay outside OpenClaw. Success/error/manual execution payload retention is disabled;
the workflow has no schedule, messages, writes or MCP exposure. The source
activation gate defaults closed. A disposable network-none container using the
exact live n8n image qualified both native GET nodes and the projection with
synthetic HA responses and no production credentials.

Connecting HA requires approval to create a dedicated metadata reader Header Auth
credential, import/publish this one new workflow using the existing HA credential,
set its source gate `configured=true`, and restart only the Mac adapter with its
explicit infrastructure registry and `--ha-transport` private config. Copy
`config/system-monitoring.json` privately, enable only its HA entry, and use
`config/system-monitoring-ha.example.json` with an absolute owner-only token file.
The token authenticates only the finite metadata webhook, never HA itself.
Verify all three bounded reads, rejected credentials/inputs and duplicate native
turn suppression before phone use. The prior Telegram approval did not cover
this production HA read route. No production workflow or credential for this
route has yet been created. Live operator reads and native investigations have
been qualified for both compute hosts; HA live qualification remains pending.

Control remains in the existing broker/operator/executor lanes. Production
executors are disabled/unprovisioned and HA/device actions are unavailable;
neither a successful read nor chat text supplies execution approval. This adds
no automatic monitoring, boot supervision or repair authority. Rollback removes
the adapter's two opt-in arguments and unpublishes only the new HA workflow,
revoking its dedicated reader credential. Preserve existing workflow state,
native sessions and outbox receipts. The Mac-awake, reboot and backup gates remain.

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
   0.25 CPU/128 MiB, and no restart policy. It mounts only its dedicated cert/key
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
6. Production observation schedules and household conversation remain gated by
   supported boot supervision/reboot acceptance and off-host backup/restore.
   The pinned external-onboarding Docker storage blocker is unresolved, both
   attempted units stay disabled, and Hermes stays independent. The canary model
   key expires 2026-10-16 at 16:15:04 UTC (18:15:04 Copenhagen); renewal is an
   operator action outside the assistant.

Rollback: unpublish only `hcOpenClawTelegram20261009` with n8n's
`unpublish:workflow --id=hcOpenClawTelegram20261009`. It requires an n8n restart;
recreate only n8n with its original two Compose files and
`/etc/homecompute/automation-tunnel.env` to restore the prior hostname mappings.
The original files are under
`/srv/homecompute/releases/automation-tunnel-20260926/deploy/automation/`.
Stop the Mac receiver/adapter/tunnel and the separate communication relay, remove
only the exact temporary INPUT rule recorded in the private backup, and revoke
only the new communication credentials/bot as appropriate. Restore the original
native deny list from the private operator backup after stopping communication.
Do not restore the entire pre-activation n8n database over subsequent household
changes; the backup is a recovery artifact, not the normal rollback mechanism.

Preserve the private polling cursor, outbox and native sessions; reconcile
`sending`/`uncertain` receipts. The rollback does not change Hermes gateway 8080,
shared Docker, the shared Telegram credential or household workflow state.

The fixed tunnel command on the adapter workstation is:

```bash
ssh -N -o BatchMode=yes -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=20 -o ServerAliveCountMax=3 \
  -R 127.0.0.1:18793:127.0.0.1:18793 home-core
```

The relay profile can then be started with reviewed `COMMUNICATION_TLS_CERT` and
`COMMUNICATION_TLS_KEY` paths, without pulling or changing Docker's daemon.
Stopping that profile and tunnel is sufficient to remove its private listener;
restore n8n's prior hostname mappings with the original Compose files if needed.
This proposal remains manually supervised and synthetic-only: operator Mac sleep,
SSH loss and the existing canary boot blocker are explicit availability limits.

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
18792, reached through an approved operator SSH tunnel if the broker is on core.
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
