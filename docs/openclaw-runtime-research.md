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
