# OpenClaw runtime and integration evidence

Evidence date: 2026-10-09. This is an isolated implementation, not a production
installation or permission to deploy. See the main OpenClaw/Codex operating
plan for host placement, task approvals, n8n boundaries, and production gates.

## Pinned artifacts and configuration

The official latest-release API returned `v2026.9.9`. Source checkout:
`bcfc88812a35243893585dbeca87ca41b48272ca`. The official GHCR release tag
`2026.9.9` resolved to OCI index
`sha256:7f10d5cc975a90b65192eaa099454fe33ce8ce2390806c61c65cd868e9ef730d`.
Its Linux amd64 image manifest is
`sha256:c4c90d19d6f428263f903fe88e1af6676fb5f7bcf5717597cb5468b913cb843a`.
The image config reports OpenClaw `2026.9.9`, the same source revision,
`USER node`, and Node `24.21.0`. Compose pins the OCI index and platform
`linux/amd64`; no mutable tag is used.
[Official release](https://github.com/openclaw/openclaw/releases/tag/v2026.9.9),
[official container instructions](https://docs.openclaw.ai/install/docker).

`config/openclaw.json` was checked against the installed official npm
`openclaw@2026.9.9` CLI. Installation was confined to `/tmp` with scripts
disabled; it did not touch the existing Mac Codex home. The npm package
integrity returned by the registry was
`sha512-3sB6ejq5smBozfBhVEDfK48wFpbkx+0f6PJ19qZjC3ab3RoFrYO58CnE2mAsbUy8sXdxdBtjQ3vkAqcW9BZXAw==`.
Source and image metadata were verified separately; this does not assert a
third-party supply-chain audit.

The schema uses `agents.entries` (a map), provider/model-scoped
`agentRuntime`, `agents.defaults.embeddedAgent`, root `memory.search`, and
`logging.audit`. Retired `agents.entries.*.default` markers were removed after
native validation warned about them. The primary route is
`homecompute/automation-moe`, API adapter `openai-completions`, explicit
runtime `openclaw`, endpoint `http://model-relay:8081/v1`, with no fallback. The trusted relay
forwards only models/chat-completions to the verified
`https://ai.home.arpa/v1` boundary with fixed TLS hostname and CA validation.
[Provider schema](https://docs.openclaw.ai/gateway/config-tools/custom-providers),
[runtime policy](https://docs.openclaw.ai/gateway/config-agents/runtime-and-cli-backends).

The parent discovery confirmed a live Flash-Next qualification route with a
262144-token server ceiling. That ceiling is native metadata; the isolated
assistant uses an operational input budget of 32768 and output cap 4096.
These smaller budgets, one active turn, provider timeout 600 seconds, and
turn timeout 660 seconds are pilot limits, not measured performance or proof
of tool reliability. Multi-step local-model reasoning,
long-running completion, and context-pressure behavior still need live
qualification. Do not advertise a production-qualified model from catalog
membership alone.

## Integration choice

| Route | What it provides | Decision for this implementation |
| --- | --- | --- |
| Bundled `codex` plugin/native runtime | Codex app-server agent turns, native threads, approvals, supervision and catalog | Keep disabled in the assistant; useful later for an explicitly separate Codex agent |
| Native Codex plugin/app support | Codex-owned apps and plugins inside an app-server thread | Distinct from delegation; needs Codex >=0.149 and a native Codex runtime; bundled version is 0.160 |
| ACP with the `acpx` backend | External harness sessions with their own lifecycle and permission integration | Supported explicit fallback, but adds another runtime boundary for this small integration |
| Noninteractive Codex CLI in a restricted worker | Auditable per-task execution, JSON events, exit status and cancellation under a broker | Selected; matches the worker's isolation and separate approval gates |
| Scoped OpenClaw tool plugin | Typed submit/status/cancel calls to a dedicated task broker | Implemented without shell, unrestricted MCP, or n8n admin credentials |

The official native plugin defaults to managed local stdio or a local Unix
control socket. Its WebSocket app-server transport is described as
experimental and unsupported for production. Local stdio starts with an
unattended YOLO posture unless constrained by requirements or explicit
permission policy; using that default inside the personal assistant would
undermine the intended separate worker boundary.
[Codex harness](https://docs.openclaw.ai/plugins/codex-harness),
[transport](https://docs.openclaw.ai/plugins/codex-harness-reference/app-server-transport),
[approval modes](https://docs.openclaw.ai/plugins/codex-harness-reference/approval-and-sandbox),
[native plugins](https://docs.openclaw.ai/plugins/codex-native-plugins),
[ACP](https://docs.openclaw.ai/tools/acp-agents).

The dependency-free ESM plugin registers exactly three optional tools through
the supported public `OpenClawPluginDefinition.register` / `api.registerTool`
API. It declares the same three names in its manifest and the config opts
into their exact names. The broker origin is fixed to `http://broker:8080`;
arguments cannot supply URLs, headers, commands, credentials, or paths.
Responses are capped at 32 KiB and reduced to task metadata/result links;
worker prompts and logs are discarded. HTTP redirects are refused and calls
have a ten-second budget. The broker remains responsible for project
allowlists, deduplication, approval, retry budgets, and durable state.
`tools.toolSearch.enabled: false` keeps this small toolset direct instead of
introducing search/describe/call indirection; generic Code Mode is also off.
[Building plugins](https://docs.openclaw.ai/plugins/building-plugins),
[tool policy](https://docs.openclaw.ai/gateway/config-tools/tool-policy).

## Mac session discovery

OpenClaw can discover existing Codex sessions on a paired Mac. This requires
the official `codex` plugin active on both computers, local node consent,
and a pairing upgrade granting only the desired versioned catalog commands:
`codex.appServer.threads.list.v1` and
`codex.appServer.thread.turns.list.v1`. Catalog listing and bounded persisted
transcript reads are separate from agent supervision. `supervision.enabled`
controls agent-facing tools; `sessionCatalog.enabled` controls discovery.
[Node session catalogs](https://docs.openclaw.ai/nodes/session-catalogs),
[supervision](https://docs.openclaw.ai/plugins/codex-harness-reference/supervision).

Paired-node continuation additionally requires `operator.admin`, an eligible
stored/idle thread, and the local `codex.cli.session.resume` permission.
Terminal resume uses a separate allowlisted relay. Importing a transcript
creates an explicit OpenClaw copy; it does not resume the original thread.
The Mac must be online for reads/continuation. No Mac pairing, plugin enable,
authentication read/copy, thread adoption, or Mac configuration change was
performed. This remains an optional operator-controlled phase, independent
of home-core worker availability.

## Persistence, memory and proactive work

The dedicated state mount retains sessions, SQLite databases/audit, and the
workspace's Markdown memory. The standard memory plugin uses deliberate
keyword-only search (`memory.search.provider: none`) so this assistant does
not silently invoke an unconfigured embedding API or download another model.
Only memory sources are indexed, not all private session transcripts. The
filesystem write tool is limited to the isolated workspace; its read-only
mounted policy instructs concise preferences/decisions and memory files.
Filesystem isolation is a hard boundary; the note about which memory filenames
to use is an instruction, not a file-by-file enforcement mechanism.
[Memory](https://docs.openclaw.ai/concepts/memory),
[FTS-only search](https://docs.openclaw.ai/concepts/memory-search).

Keep n8n execution/processed-item/notification state in n8n and long-running
repair state in the broker. The assistant remembers references and confirmed
human decisions. Do not move either state store into the memory directory.
Memory dreaming, heartbeat cadence, cron, HTTP hooks, MCP, browser, ACP,
public discovery, auto-update, and hosted model-catalog refresh are disabled
in this initial config.

OpenClaw's supported `/hooks/agent` can later accept narrowly scoped events
with a dedicated token, `allowedAgentIds`, restricted session-key prefixes,
and idempotency keys. The hook token must differ from the gateway token.
`/hooks/wake` requests a heartbeat; admission is not proof of successful
execution or notification. Start with the broker/n8n integration instead of
opening unrestricted event ingress. Internal lifecycle hooks are a separate
mechanism. Any notification channel needs its own approved integration; none
is implied by a submitted task.
[HTTP hooks](https://docs.openclaw.ai/gateway/config-hooks),
[internal hooks](https://docs.openclaw.ai/automation/hooks).

## Proposed inactive n8n hook contract

The pinned 2026.9.9 source/schema supports the following **future** config.
Current checked-in hooks remain disabled; add the fourth secret and ingress
only after explicit operator approval, using sops-nix and a separate random
hook token exported by the trusted entrypoint. `hooks.token` accepts an
expanded string, not a SecretRef object.

```json
{
  "hooks": {
    "enabled": true,
    "token": "${OPENCLAW_HOOK_TOKEN}",
    "path": "/hooks",
    "allowedAgentIds": ["assistant"],
    "allowRequestSessionKey": false,
    "allowedSessionKeyPrefixes": ["hook:"]
  }
}
```

An inactive n8n HTTP node can prepare this fixed request, authenticated with
`Authorization: Bearer <dedicated hook token>` and an `Idempotency-Key`
header derived from the workflow's stable event ID (max 256 characters):

```json
{
  "message": "Synthetic issue context with a stable project and issue key",
  "name": "n8n investigation",
  "agentId": "assistant",
  "sessionMode": "isolated",
  "wakeMode": "now",
  "deliver": false
}
```

`sessionKey` must be omitted: supplying one fails when caller keys are disabled.
Without `defaultSessionKey`, OpenClaw generates `hook:<uuid>` and creates an
isolated run. Construct these fields explicitly; do not spread upstream event
JSON into the HTTP body. Drop external `sessionKey`, `model`, `thinking`,
`timeoutSeconds`, `channel`, `to`, `accountId` and callback/URL fields. The
normalizer ignores unknown keys rather than enforcing a closed object schema;
a caller token does not identify a trusted person. Model allowlists and the
same restricted agent tools must remain in force.

A `200 {ok:true, runId}` proves admission only. Optionally set the fixed field
`waitForCompletion: true` to receive categorical `completion.status` and
`replyDisposition`; it does not return the assistant's answer text and failed
post-admission runs can still return HTTP200. Hook idempotency is in-memory,
expires five minutes after terminal completion, and clears on restart; it
cannot replace the broker's durable `(project, issue_key)` deduplication.
`deliver:false` prevents hook announcement, while tool denies separately
prevent outbound message tools. No arbitrary callback mechanism is needed.
[Official hook contract](https://docs.openclaw.ai/gateway/config-hooks),
[pinned normalizer source](https://github.com/openclaw/openclaw/blob/bcfc88812a35243893585dbeca87ca41b48272ca/src/gateway/hooks.ts).

## Deployment and maintenance gates

The service is opt-in via Compose profile `assistant`. It publishes only
loopback `127.0.0.1:18791` because Hermes already uses 18789. Use an approved
SSH tunnel through Tailscale, for example
`ssh -N -L 18791:127.0.0.1:18791 mads@home-core`, then the localhost URL.
There is no new public proxy or Tailscale daemon in this container. The
container must listen on `lan` internally for Docker port forwarding; that
setting does not publish a LAN host port. Keep gateway token authentication.
[Tailscale access](https://docs.openclaw.ai/gateway/tailscale),
[gateway bind semantics](https://docs.openclaw.ai/gateway/config-gateway).

Before an approved trial, install the secret-free config at
`OPENCLAW_CONFIG_FILE` with mode 0600 (UID1000 owner) or 0640/0440 (restricted
reader group); create the state/workspace directories mode0700 UID1000;
install root-owned read-only plugin/policy sources; supply the existing public
Caddy CA certificate; and provision three distinct secret files. The model
credential must be a dedicated LiteLLM virtual key scoped to `automation-moe`,
never its master key. The broker token must have no operator approval or
publish authority. Secret values are loaded only into the Gateway process by
the entrypoint, never rendered into JSON. Operator credentials stay elsewhere.

The assistant has one CPU, 1536 MiB RAM, equal swap cap, 128 PIDs, a read-only
root filesystem, dropped capabilities, `no-new-privileges`, bounded tmpfs/logs,
and no Docker socket, host repositories, SSH keys, n8n/database credentials,
or Mac state mounts. Its internal Docker network has no direct default route
to production or the internet. Model requests bypass the generic proxy only for `model-relay` through the
configured `NO_PROXY`; the supported explicit
`request.proxy.mode: env-proxy` handles that selection. The relay fixes the
upstream host, paths, TLS CA/SNI and limits. This prevents a broad Caddy CONNECT
grant from reaching other services through a different TLS hostname.
The included egress service must allow only approved CONNECT destinations;
Docker internal networking alone cannot restrict destinations after traffic
passes through that proxy. No generic fetch tool is allowed.

`OPENCLAW_CONFIG_READONLY=1` prevents config changes. A real startup conflict
was found: the official Docker activation entrypoint always runs
`doctor --fix`, which fails when config is externally managed. The custom
entrypoint therefore runs the foreground Gateway directly. Fresh immutable
state startup was verified. For retained-state upgrades, stop all writers,
back up the entire state, run pinned candidate Doctor against a **private
writable copy** of config/state during offline maintenance, inspect changes,
reconcile them into declarative source, and validate the migrated copy before
switching images/state. Do not remove lock files or downgrade only the image;
SQLite schema rollback needs its matching pre-upgrade state.
[Externally managed config](https://docs.openclaw.ai/cli/config),
[Docker state migrations](https://docs.openclaw.ai/install/docker).

Backup is an operator-approved encrypted off-host copy of stopped state,
including workspace/SQLite/sidecars and matching declarative version. Exclude
secret files and private worker clones where unnecessary. Restore into an
isolated network and verify task/memory continuity before considering it
qualified. Stop the assistant and broker/worker to freeze new actions; broker
cancellation/disable switches must terminate an already running worker.
Docker health restart policy alone is not an automatic unhealthy-container
recovery mechanism; monitoring must detect degraded readiness. The pinned
embedded runtime separately allows up to eight transient model retries within
a 90-second outage window; observed invalid-endpoint attempts exceeded an
individual 45-second turn setting. These retries do not approve or duplicate
broker tasks, but a provider timeout is not a hard end-to-end outage budget.
Verify cancellation and use an external request/job deadline for that boundary.

## Verification and limits

Verified locally with synthetic credentials/private temporary state:

- Official OpenClaw 2026.9.9 `config validate --json`: valid, zero warnings.
- `plugins inspect homecompute-broker --runtime --json`: loaded, exact three
  optional tools, zero diagnostics, no dependencies/hooks/HTTP routes.
- `security audit --json`: zero critical, zero warnings after temp config
  permissions were restricted; metadata-only attack-surface finding remains.
- Seven Node tests passed: fixed origin/auth/routes, private-output stripping,
  path/URL/extra-field injection rejection, limits, cancellation, missing
  credentials, malformed responses and sanitized failures.
- Fresh-state native Gateway with read-only config and synthetic unavailable
  localhost model: `/healthz` live; `/readyz` ready with no failing checks;
  stopped cleanly with SIGTERM. This verifies initialization, not inference.
- Shell entrypoint syntax and staged Compose interpolation validated.
- An actual native OpenClaw turn reached the live `automation-moe` route
  using the existing scoped client key in process memory and the trusted
  public CA. After disabling tool-search indirection, the 1024-output-token
  probe completed in **6176 ms**: two assistant turns (submit + final),
  `successfulToolNames: [homecompute_task_submit]`, requested/effective
  `automation-moe`, native `openclaw` runtime, no reroute, and one durable
  broker task left **pending**. No worker, approval, cloud Codex, GitHub,
  private context, or production workflow was invoked. Its temporary local
  broker/plugin used synthetic policy/state only. Sanitized evidence is in
  [runtime validation](openclaw-runtime-validation.json).
- The earlier 128-output-token probe failed with incomplete/malformed tool
  output and no task submission; initial harness attempts also failed from
  an unexpanded public CA path and container-only DNS name on the Mac.
  Those failures were not production endpoint failures. The final probe
  used direct TLS upstream, so it does **not** qualify the deployment relay,
  container isolation or 4096-token production budget.

To repeat that synthetic live probe with an isolated pinned CLI:

```bash
python3 scripts/verify-openclaw-runtime.py \
  --cli /path/to/isolated/openclaw-2026.9.9 \
  --report /tmp/openclaw-runtime-probe.json
```

The script fixes the upstream to the local inference boundary, consumes the
existing model client key without sourcing shell code or writing credentials,
creates a temporary broker on an ephemeral loopback port, submits one pending
synthetic investigation, applies a 120-second external deadline, and stops and
removes its temporary services/state. `--report` contains synthetic metadata
only. It is a tool-loop smoke test, not a worker/deployment acceptance test.


The Mac Docker daemon is unavailable, so the pinned Linux image was inspected
through official registry metadata but not started here. CPU/RAM enforcement,
proxy deny behavior, mounted ownership, reboot/restart, retained-state
migration, and backup restoration need an approved isolated Linux trial.
Broader native multi-step/context qualification, real worker authentication/execution,
GitHub draft-PR publication, and notification delivery are separate acceptance
gates. No deployment, secret provisioning or autonomous production writes
were performed by this runtime subtask.

## Coding integration recheck and dedicated worker candidate

The 2026-10-09 integration recheck confirms home-core kernel 6.18.48,
Docker 29.7.2 and the pinned validation image's Codex 0.145.0. No native Codex
installation, dedicated worker service identity, intended worker secrets or
deployed project policy exists. The checked-in project policy is still empty.
Default Docker returns EPERM on user namespaces; normal Bubblewrap fails
workspace-write before checkout or authentication. The deprecated Landlock
route is not enabled. An isolated numeric-UID native probe passes filesystem
and network denials, but does not qualify a host-direct production worker.
[Updated worker receipt](openclaw-worker-validation.json) preserves both findings.

The broker now projects five requested statuses while preserving its exact
approval states: pending has status queued and execution approval required;
review has status completed and publication approval required; publishing has
status running. Completion of a coding proposal therefore does not mean that
it was published, merged or deployed. Safe results expose test outcome, changed
file count and session ID; public PR artifacts use bounded GitHub links.
Stable `(project, issue_key)` submissions reject conflicting content. Unchanged
heartbeats update only the private lease, producing no progress notification.

`GET /task-events?after=SEQ&limit=100` uses the existing ledger's event sequence
and stored public snapshots. The optional dedicated snapshot role can only read
task/action metadata; it cannot submit, cancel, claim, approve or publish. Old
events predating the snapshot migration explicitly report a missing snapshot.
Consumers must reconcile such a gap rather than pretend that current metadata
reconstructs historical events. The recent 100-task list is still bounded.

Ordinary custom tools in the pinned managed runtime cannot satisfy its native
replay-safe receipt check. Broker tools stay denied and the managed plugin stays
disabled. The existing standalone plugin was tested through an actual isolated
OpenClaw 2026.9.9 turn: one synthetic pending submission in 6.395 seconds, no
approval, worker execution or PR. This is not managed replay-safety evidence.
[Broker validation](openclaw-runtime-validation.json) records this distinction.
The trusted phone adapter provides explicit `/code`, `/task` and `/cancel-task`
commands without a model turn; autonomous native delegation remains gated.

### Guest installation design before approved provisioning

The new `deploy/codex-worker/homecompute-codex-worker.service` and
`qualify_native.py` prepare the same worker for a **separate** Ubuntu worker VM
on home-core. This design preceded the approved deployment recorded below.
Do not install them on the shared host or existing agents/Hermes guest. The
unit requires separately qualified VM provisioning, broker TLS, proxy and
firewall boundaries. Use the immutable Ubuntu image
URL/hash already selected by `modules/nixos/agents-vm.nix`, with separate state,
identity and network. Keep the current Docker and Hermes settings intact.

After reviewing and provisioning that separate guest, its operator must:

1. Install distro Bubblewrap and its scoped AppArmor profile where required,
   Python, Git, CA certificates and Node/npm. Do not relax global AppArmor or
   user-namespace policy. Install root-owned worker/policy/qualification sources
   and both locked package files in `/opt/worker`; run `npm ci --ignore-scripts`.
   The qualification helper requires exactly `codex-cli 0.145.0`.
2. Create the locked `codex-controller` system account without a home/login;
   reserve numeric job UIDs 10000–59999. Mount a distinct 4–12 GiB work filesystem
   at `/srv/codex-work`, controller-owned mode0711. A same-device bind mount is
   rejected. Review the VM's CPU/RAM, total disk and network limits separately.
3. Install reviewed `/etc/homecompute-codex/projects.json` and root0600
   `worker.env`. The environment fixes the private HTTPS broker origin, trusted
   CA, and CONNECT proxy. Enforce outbound access only to that broker and proxy;
   jobs must not reach production services, SSH, Docker or physical devices.
4. Provision separate worker, Codex-auth and read-only checkout credentials in
   the guest's protected secret manager. The unit uses `LoadCredential`; child
   environments drop credential-directory/control tokens and all capabilities.
   Operator/publisher/snapshot credentials stay in their own trusted services.
   Codex's scoped API key remains visible to its own process and must be budgeted
   and revocable. No Mac authentication is copied and no secret enters Git.
5. Verify the installed unit with `systemd-analyze verify` and keep its `ENABLED`
   marker absent. The checked unit has no `[Install]` section. Run credential-free
   qualification in its exact service context and test VM egress, process cleanup,
   protected secrets, broker permissions and bounded storage before enabling it.
6. After execution approval, qualify one allowlisted authenticated job, duplicate
   suppression, cancellation, tests/results and phone status. Publication requires
   its separate reviewed digest; merge/deployment remain separate approvals.

Unit syntax was verified in a temporary Ubuntu file. Current worker code passed
disposable Linux identity/secret/temp/cleanup checks and explicit capability
clearing under a nonroot controller. Neither test provisions or qualifies the
new VM. Stop the candidate unit and remove its `ENABLED` marker to disable it;
retain work/evidence and reconcile the existing ledger before any new incident.
Do not restart an uncertain task or delete receipts to obtain a retry.

### Approved dedicated VM provisioning, 2026-10-09

The user approved provisioning and selected HomeCompute with a new dedicated
API key. `scripts/setup-codex-vm.py` installed a separate native KVM guest,
`codex-worker`, from the exact Ubuntu image hash above. Its kernel is
6.8.0-139-generic, with two vCPUs, 8 GiB RAM, a 32 GiB root disk and a distinct
12 GiB `/dev/vdb` disk whose virtio serial is `hc-codex-work`. The guest carries
the root-owned, mode0444 `/etc/homecompute-codex-vm.json` identity marker required
by the guest installer. No host directory or Docker socket is mounted inside
the guest; it receives virtual disks, networking and entropy devices only.
The administrative private SSH key stays on the Mac.

Host units live in the writable `/etc/systemd/system.attached/` search path,
with dedicated `multi-user.target.wants` links, because NixOS's ordinary
`/etc/systemd/system` is an immutable generated symlink. Systemd verified those
units and the running target includes them. Their pinned Nix dependencies have
dedicated GC roots. This installation did not switch the shared NixOS system
or change the existing agents VM. Host QEMU runs as `homecompute-codex-vm`, with
KVM/TUN access only, no new privileges, protected host filesystems, a 10 GiB host
memory ceiling and a 200% CPU ceiling. The loaded unit reports disabled because
`systemctl enable` does not manage the attached directory; the explicit target
links provide persistence. A host reboot has not yet been qualified.

The separate `br-hc-codex` bridge is `10.77.21.1/30`, with guest
`10.77.21.2` and `tap-hc-codex`. Dedicated INPUT/FORWARD guards precede the
existing Docker, Tailscale and NixOS chains. Bootstrap temporarily allows guest
public HTTP(S) and its dedicated host DNS. Public DNS upstreams did not respond
from this host; CoreDNS instead forwards to the host's trusted resolver.
`sudo python3 /etc/homecompute/codex-vm/setup.py --lock-egress` removes the
maintenance marker and closes guest DNS and every forwarded destination,
leaving only bridge-local broker TLS19444 and CONNECT3129. The guard rejects
IPv6; the guest also disables IPv6.

Actual bootstrap probes denied guest access to host SSH, production host SSH,
the agents guest, the control plane, Tailscale DNS and link-local metadata,
while public Ubuntu HTTPS returned200. After the worker installer completed,
maintenance egress was closed with the firewall's `ExecReload`, preserving the
QEMU PID. Actual closed-policy probes deny direct public HTTP(S), DNS over both
TCP and UDP, host/production/agents SSH, control-plane, Tailscale and metadata
destinations. The broker's exact CA and IP name verify; wrong names and missing
CA fail certificate verification. Official API/GitHub CONNECT tunnels return200
without sending authenticated requests; unknown/private targets return403.
Unauthenticated broker routes return403. Authenticated Codex execution and
independent proposal validation are recorded below; original failure states
remain terminal. HomeClaw is saved outside Git and installed root0400 in the guest.

The guest SSH ED25519 fingerprint was recovered from QEMU's host-controlled
serial output and pinned before the first SSH connection:
`SHA256:WLTBMi7vQb6pML+biFtl67Wk+6p/BrVPAZjmXXcDr5g`.
The Mac pin is `~/.config/homecompute/openclaw/codex-worker-known-hosts`.
Connect with the existing admin identity, `ProxyJump=home-core`,
`StrictHostKeyChecking=yes`, and that `UserKnownHostsFile` to
`codex-operator@10.77.21.2`. Do not replace this with unverified `ssh-keyscan`.
To stop the VM while retaining evidence and closing maintenance egress, run
`sudo python3 /etc/homecompute/codex-vm/setup.py --stop`. Its disks stay under
`/srv/state/codex-vm`; stopping does not clear the existing broker ledger.

The initial installation's firewall service restart also restarted its
dependent guest and interrupted package unpacking. Guest-only APT/dpkg recovery
restored the affected package/cache files before installation and qualification
completed. Live firewall changes now use `ExecReload`; an actual reload kept the
same QEMU PID and boot timestamp. No coding job was launched or retried during
that recovery.

The approved VM-only restart drill then exercised graceful QMP powerdown and
guest startup at 2026-10-09 20:07:17–18UTC (22:07:17–18Europe/Copenhagen).
The new guest boot ID is `e77e10f2-35dc-4cb5-8d5a-9b12ef4fb75f`; the pinned
SSH identity matched, `/srv/codex-work` mounted automatically from `/dev/vdb`
with `rw,nosuid,nodev`, and the scoped `bwrap` AppArmor profile loaded in enforce
mode. The `ENABLED` marker and Codex API key were absent, and the worker remained
inactive. The complete closed-network denial/allowed-proxy/TLS checks passed
again after guest boot. Existing agents QEMU PID3019518 and its start timestamp
were unchanged. This proves guest restart persistence; a home-core reboot was
not performed or qualified.

The worker's exact-unit credential-free qualification passed again after that
boot at 20:09:12UTC. It observed Codex0.145 invoking `/usr/bin/bwrap`, the
child `bwrap//&unpriv_bwrap` AppArmor label in enforce mode, denied nested
privileged namespaces, and successful descendant cleanup under the closed
network policy. This qualifies the sandbox and guest restart; it does not
authenticate a model request or execute a broker coding task.

### Authenticated canary and reconciled validation, 2026-10-09

The approved HomeClaw key authenticated one Codex run on task
`822cf264-85c6-4d1a-ad5a-292fa30f582b`. It produced the requested publish-denial
regression. The immutable baseline's sparse-file fixture exceeded the old10MiB
file ceiling, so the broker accurately retains `failed`; no task is replayed.
Only trusted baseline/postcheck children now use a257MiB ceiling. Codex/preflight
and stored evidence remain10MiB, with unchanged scan/output,12GiB disk and VM
boundaries. Native baseline25/25 and saved proposal26/26 pass; independent
mutation proves the regression catches incorrect publication authority. No
additional model run or PR occurred. The worker is stopped, `ENABLED` absent.
The authenticated `/task` reply reached Telegram; terminal alerts await existing
quiet hours. The root-owned operator client is installed at
`/opt/homecompute/codex-broker/assistant-task.py`; use its explicit private
`--token-file` for approval/review. Exact receipts are in the existing worker
and communication validation JSON files. The supervised Mac must stay awake.
