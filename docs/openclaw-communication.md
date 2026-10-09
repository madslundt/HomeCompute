# OpenClaw communication and notification activation proposal

Verified 2026-10-09. The private operator console works against the existing
synthetic managed canary. External notifications and the n8n conversation route
are implemented and tested in isolation, but **are not connected to production**.
The operator has been asked to select a channel; no destination was authorized
during this implementation. The safe available entry is the authenticated SSH
console. No workflow, production credential, dashboard exposure, Docker setting,
Hermes configuration or attempted supervisor unit was changed by this task.

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

Live two-way synthetic recall passed, including a second verification through
the HTTP adapter. The current native canary still permits private memory writes;
the console refuses to treat a write receipt as safely replayable. It grants no
additional tools or production capabilities.

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

1. Select the authorized private channel, chat and human sender. Discovery found
   a private notification audience used by the battery/HomeCompute weekly reports
   and a distinct family audience. This task printed only their fingerprints.
   The shared bot has a disabled inbound trigger; no live inbound OpenClaw route
   was discovered. Confirm the bot's existing ingress ownership before pairing.
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
   **new inactive workflow**. Bind the existing approved Telegram credential and
   the dedicated delivery bearer-header credential. Set the selected audience
   and chat in its configuration node, then set `configured:true`. Keep retries
   off on sending nodes. The workflow validates destination and Telegram's chat
   and message receipt before acknowledging the outbox. Its default fails closed.
4. Import `n8n-conversation-workflow.json` as a new inactive subworkflow. Pin
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
   Then review a single n8n-owned delivery/collection schedule separately;
   neither template currently includes a schedule or public webhook.
6. Production observation schedules and household conversation remain gated by
   supported boot supervision/reboot acceptance and off-host backup/restore.
   The pinned external-onboarding Docker storage blocker is unresolved, both
   attempted units stay disabled, and Hermes stays independent. The canary model
   key expires 2026-10-16 at 16:15:04 UTC (18:15:04 Copenhagen); renewal is an
   operator action outside the assistant.

Rollback: deactivate only the new communication workflows and any separately
approved schedule/router branch, stop the adapter/tunnel, and revoke only its
three dedicated transport credentials. Preserve the private outbox and native
sessions; reconcile `sending`/`uncertain` receipts. Restore the prior router
definition if it changed, retaining its bot's original webhook/cursor ownership.
This stops external delivery without touching gateway 9123, Hermes gateway 8080,
shared Docker, the shared Telegram credential or household workflow state.

After approval, the fixed tunnel command on the adapter workstation is:

```bash
ssh -N -o BatchMode=yes -o StrictHostKeyChecking=yes -o ExitOnForwardFailure=yes \
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
suppression and scoped synthetic incident analysis. Outbox acknowledgements
were mock receipts; no Telegram message was sent. Twenty-six transport and
eleven console tests pass, plus five n8n routing/receipt tests. The exact current
n8n image also executed the notification workflow in a disposable, network-none,
read-only container with dropped capabilities, 0.5 CPU and 768 MiB memory.
The external Telegram sender and adapter HTTP endpoint were synthetic surrogates
in that engine check; claim/validation/ack workflow nodes ran in real n8n.

```bash
python3 tests/openclaw-chat-test.py
python3 tests/openclaw-communication-test.py
node --test tests/openclaw-n8n-communication.test.mjs
# Explicit opt-in live synthetic checks; no production sending credentials.
python3 scripts/verify-openclaw-communication.py --live
python3 tests/openclaw-n8n-engine-test.py --live
```

The inherited AGENTS security-scan command was attempted without permitting a
package installation; npm reported `ENOTCACHED` for `@Codex-flow/cli@latest`.
The dependency-free implementation received a separate code/security review and
boundary tests. An unavailable external scanner is not recorded as a passed scan.
Actual Telegram receipt delivery, native browser pairing, private HTTPS ingress,
unattended adapter supervision and production monitoring have not been qualified.
