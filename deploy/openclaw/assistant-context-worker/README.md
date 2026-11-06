# Assistant context worker

This optional role supports only bounded owner/personal/private analysis,
summary, and comparison requests. It runs in the native OpenClaw `2026.9.1`
runtime as `assistant-context-worker`, with `inference/automation-moe`, a
dedicated workspace, zero tools, zero memory search, no messaging, no
delegation, and no cloud fallback. It cannot use the capabilities granted to
Atlas or any existing specialist. This is an analysis-only role; it does not
read sources or operate controllers. Existing task, coding, calendar, Home
Assistant, and messaging controllers keep their own authority.

## Native transport contract

The adapter is pinned to OpenClaw source tag [`v2026.9.1`](https://github.com/openclaw/openclaw/tree/v2026.9.1).
The following source files define the protocol used here:

- [`AgentParamsSchema`, `AgentWaitParamsSchema`, and `SessionsAbortParamsSchema`](https://github.com/openclaw/openclaw/blob/v2026.9.1/packages/gateway-protocol/src/schema/agent.ts)
- [`AgentParamsSchema` admission fields and UUID-to-run-ID derivation](https://github.com/openclaw/openclaw/blob/v2026.9.1/src/gateway/agent-turn/agent-request-preflight.ts)
- [`agent` admission receipt shape](https://github.com/openclaw/openclaw/blob/v2026.9.1/src/gateway/agent-turn/agent-run-admission-phase.ts)
- [`agent.wait` terminal result projection](https://github.com/openclaw/openclaw/blob/v2026.9.1/src/gateway/agent-turn/agent-turn-service.ts)
- [`sessions.abort` exact session/run cancellation](https://github.com/openclaw/openclaw/blob/v2026.9.1/src/gateway/server-methods/sessions-abort.ts)
- [`sessions.delete` and its transcript lifecycle](https://github.com/openclaw/openclaw/blob/v2026.9.1/src/gateway/server-methods/sessions-delete.ts)
- [`sessions.delete` protocol schema](https://github.com/openclaw/openclaw/blob/v2026.9.1/packages/gateway-protocol/src/schema/sessions-delete.ts)
- [Gateway CLI `call` command and JSON parameter protocol](https://github.com/openclaw/openclaw/blob/v2026.9.1/src/cli/gateway-cli/register.ts)

Submit uses the stable request UUID as native `idempotencyKey`; the pinned
source derives `runId` from that field. A response is accepted only when the
native receipt returns the same run UUID, fixed agent ID, expected session key,
`status: accepted`, and integer `acceptedAt`. `agent.wait` is used for native
status and terminal reply/receipt reconciliation. A wait timeout without a
terminal receipt is an unknown observation, not completion or cancellation.
Cancellation calls `sessions.abort` with the fixed worker agent ID, exact
session key, and exact native run ID; only an `aborted` receipt for that run is
reported as cancelled. A lost submission response or process restart leaves
the request uncertain until the native gateway provides matching evidence; the
adapter never replays an uncertain submission.

Requests may carry a finite set of parent-validated source/object/revision
references. A deletion tombstone scrubs the local task text, result, evidence,
and source references before asking the exact native run to abort. The pinned
`sessions.delete` API can remove a session record, but on ordinary durable
sessions it archives the transcript (`deleteTranscript`); its permanent
transcript deletion path is reserved for incognito sessions, which this
assistant run contract cannot use. Therefore source deletion remains
`purge_pending` until the owning OpenClaw archive-retention lifecycle confirms
removal. The controller retains only a hashed source tombstone and native task
metadata during that hold. It never claims that an archive operation erased
source-derived native transcript content.

## Plan and guarded apply

The core controller invokes this guest-side adapter over the existing fixed
SSH transport with dedicated external identity `/etc/homecompute/assistant-context-worker/id_ed25519`
and known-hosts pin `/etc/homecompute/assistant-context-worker/known_hosts`.
The home-core service receives these only through systemd credentials named
`openclaw_worker_ssh_key` and `openclaw_worker_known_hosts`. The guest gateway
credential is not copied to home-core or Hermes. The adapter accepts one
bounded JSON request over stdin and returns a metadata/result receipt over
stdout. It rejects caller-supplied principal, origin, destination, role,
model, channel, or transport settings.

The key must be restricted on the guest too. The source template
[`authorized_keys.template`](authorized_keys.template) binds the dedicated
Ed25519 key with OpenSSH `restrict` and a forced command that runs only
`nemoclaw agent-openclaw exec -- python3
/sandbox/.openclaw/homecompute-assistant-context-worker.py --stdio`. The fixed
environment sets the pinned gateway port and executable search path. The key
cannot open a shell, PTY, forwarding channel, or user startup hook. Existing
operator keys remain byte-for-byte unchanged.

Provision only the public half at
`/etc/homecompute/assistant-context-worker/id_ed25519.pub` on the agents VM,
root-owned with mode `0600`. The private identity remains on home-core at the
external path above. From a root shell on the Ubuntu `agents` KVM guest, use
the published full HomeCompute source SHA for every command:

```sh
python3 scripts/setup-assistant-context-worker-ssh.py plan --revision FULL_SHA
python3 scripts/setup-assistant-context-worker-ssh.py apply --revision FULL_SHA \
  --expected-authorized-keys-sha256 SHA_FROM_PLAN
```

The installer verifies that the SHA is an ancestor of the pinned
`origin/master`, that the checked-out installer and template match that
published revision, and that the host is the root-owned Ubuntu agents VM. It
rejects changes to any existing managed worker-key entry, preserves all other
`authorized_keys` records, and stores a private exact preimage and receipt
before atomically installing the new file. To roll back, use the receipt ID
and installed hash returned by apply:

```sh
python3 scripts/setup-assistant-context-worker-ssh.py rollback --revision FULL_SHA \
  --backup-id BACKUP_ID --expected-installed-sha256 INSTALLED_SHA_FROM_APPLY
```

Rollback refuses a changed source, receipt, or authorized-keys file. This
source implementation does not provision the external keypair, change SSH
daemon policy, restart OpenClaw, or enable delegation on home-core.

Run `setup-assistant-context-worker.py plan` inside the selected guest to show
the reviewed config hash and candidate. Apply requires the reviewed
`--expected-config-sha256` from that plan. The installer refuses a role roster
other than the source's twelve existing IDs, an existing role with different
configuration, unreviewed worker file drift, or a config changed since plan.
It modifies only `agents.entries.assistant-context-worker`, validates with the
pinned native `openclaw config validate`, retains a hash-bound backup, and does
not restart the gateway. The rollback command requires that exact backup ID and
the exact installed config hash; it preserves worker/session data.

Managed network/credential provisioning, role activation, gateway restart,
production enablement, and live verification remain separate opt-in gates.
