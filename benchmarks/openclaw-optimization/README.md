# OpenClaw performance qualification

This is an isolated native canary, not a deployment. Production configuration,
installed runtime files, existing conversations and channels are not written.
The local `automation-moe` model and managed `inference.local` credential path
are retained. Qualification consumes the shared inference service; run serially.

The measured results and promotion gates are in
[the 2026-10-30 report](../../docs/benchmarks/openclaw-performance-2026-10-30.md).
The reasoning candidate is deliberately **not** in the canonical configuration:
one combined reasoning/schema tool run looped and timed out.

## Source and variants

- `scripts/openclaw_performance.py` builds pure candidates. Browser changes
  require the exact qualified native module SHA-256; unknown runtime drift
  aborts before modification.
- `deploy/openclaw/safety-plugin/browser-schema.mjs` projects the native schema
  onto the existing independent browser guard. It changes the model-facing
  description and schema, preserving the guard and public-host restrictions.
- `loader.mjs` loads that projection in the canary process only. It does not
  patch the installed module or register anything in the running gateway.
- `payload-probe.mjs` exercises the installed provider without network access:
  current/flag-only off omit the effort; the complete candidate sends `none`;
  explicit `high` and a tool-result round trip survive serialization.
- `schema-probe.mjs` measures the actual native schema and description.

`qualify-openclaw-performance.py` accepts `before` (current model metadata),
`reasoning` (explicit off control), `schema-only` (compact browser surface), and
`schema` (both candidates). Each command creates fresh private state and copies
only the selected role's policy file. Channels, memory, delegation and writes
are absent. The native deadline is 30 seconds, with a 60-second outer bound;
production's loop-detection settings are copied. The model output limit is
1024 tokens. This harness does not reproduce the entire production prompt.

The supplied-evidence cases require no tools. `--case read-fixture` creates a
synthetic rice/lentils file and requires exactly one successful `read`, total
50 DKK, no other tools, and a completed result. Semantic and strict-JSON
results are separate. Tool failures and failed runs must remain in the report.
A timeout is not a latency success. Report prompt input as `input + cacheRead
+ cacheWrite`, so prefix-cache reuse cannot masquerade as prompt reduction.

## Run through the managed lifecycle

Stage the selected reviewed source on the agents guest, then use supported
NemoClaw `upload` and `exec`. A directory upload nests its source basename;
inspect that returned location before invoking the runner. Always select the
OpenClaw gateway explicitly, using the operator's configured executable PATH:

```sh
export NEMOCLAW_GATEWAY_PORT=9123
nemoclaw agent-openclaw upload /tmp/oc-perf-source /sandbox/oc-perf-source
nemoclaw agent-openclaw exec -- python3 \
  /sandbox/oc-perf-source/oc-perf-source/scripts/qualify-openclaw-performance.py \
  --source /sandbox/oc-perf-source/oc-perf-source \
  --output /sandbox/perf-UNIQUE-before-budget-en \
  --variant before --case budget-en
```

The stage needs both scripts, this benchmark directory, the browser-schema
helper and `benchmarks/assistant-comparison/cases.json`. Output must be a new
`/sandbox/perf-*` directory outside production state. Repeat with unique paths and rotate variant order;
never overlap managed commands, which share the native host lock. Stop a matrix
on failure and reconcile it before another test. Never retry an uncertain
outcome automatically. Read only that canary's receipts and synthetic state.
Keep stdout/stderr and transcripts private; commit only sanitized metrics.

For an offline payload check inside the same native sandbox:

```sh
nemoclaw agent-openclaw exec -- node \
  /sandbox/oc-perf-source/oc-perf-source/benchmarks/openclaw-optimization/payload-probe.mjs
```

The projection is gated on the pinned OpenClaw 2026.9.1 module. Requalify it
when the runtime or protected browser policy changes. It is not a general
upstream browser replacement. A public-browser round trip, real specialist
workflow, long reasoning, channel latency, voice and production load remain
separate qualification work.

## Activation and rollback gate

There is no production apply command in this canary. Keep it that way until the
combined tool-loop failure is understood and a source-owned, hash-bound
installer/rollback is reviewed. The live gateway has `gateway.reload.mode=off`;
loading runtime/schema changes requires the documented managed gateway restart.
Do not enable hot reload, alter the live configuration or replace runtime files
to bypass the request to keep OpenClaw running. A future release must preserve
exact pre-change config/runtime/helper backups, use the selected published
revision, apply through the owning lifecycle, restart in an agreed idle window,
then verify authenticated channels, affected tool behavior and drift. Restore
only those exact backed-up files on rollback; do not reinstall the team bundle
or replay conversations.
