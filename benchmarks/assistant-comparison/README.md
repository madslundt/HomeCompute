# Disposable Rakazo runtime comparison

This is a synthetic runtime probe on the operator workstation, not a household
Rakazo deployment. Tracking: [issue 46](https://github.com/madslundt/HomeCompute/issues/46).
The pinned image contains upstream revision
`953160d53fa594e6712b42ff038dd886aafc5bd6`; verify its OCI revision label before use.

OpenClaw keeps its running gateway, channels, permissions and model. No production
account, MCP credential, computer worker, database, Docker socket or host profile
is mounted into Rakazo. The only writable mount is the private qualification
output directory. The fixed loopback relay verifies the private CA and SNI for
`ai.home.arpa`, reaches home-core's existing LAN gateway, permits only
`automation-moe` chat completions, caps output at 1,024 tokens and permits only
one in-flight relay request. It uses the gateway's existing approved LAN
inference admission, not an independent candidate virtual key. This is not a
qualification of per-consumer credential isolation. No credential is created or
copied and no cloud model fallback is configured.

The Docker Desktop host must resolve `host.docker.internal`. No computer/browser
tools are supplied to the runtime; Docker's normal network is not an egress
allowlist. Do not extend this fixture to private data or arbitrary tools. A full
candidate needs its own limited credential and reviewed hosting/egress policy.

## Prepare and run

Inspect `git status --short --branch`, source changes, current live service and
model readiness, resource pressure and existing tasks first. Run serially during
an acceptable load window. Keep raw receipts outside Git in a mode-0700 directory:

```sh
comparison_evidence="$HOME/.local/state/homecompute/assistant-comparison/UNIQUE"
mkdir -m 700 -p "$comparison_evidence"
python3 scripts/assistant-comparison.py relay
```

Leave the loopback relay in that terminal. In another terminal, run one fixture
at a time, waiting for each command to finish:

```sh
python3 scripts/assistant-comparison.py direct --case budget-en --output "$comparison_evidence"
COMPARISON_CASE=budget-en COMPARISON_REASONING_CONTROL=true COMPARISON_EVIDENCE="$comparison_evidence" \
  docker compose -f benchmarks/assistant-comparison/compose.yaml run --rm --no-deps runtime
python3 scripts/assistant-comparison.py native --case budget-en --output "$comparison_evidence"
```

Supported fixture IDs are in
[`cases.json`](cases.json). Each native
invocation uses a fresh session through the pinned SSH/NemoClaw path, with no
`--deliver`. Existing receipt paths refuse overwrite. A timeout is uncertain:
inspect the original session and stop the matrix; never replay it to obtain a
clean result. Normal probes deliberately leave their synthetic sessions as
durable application data, rather than deleting agent history.

`COMPARISON_REASONING_CONTROL=true` means the endpoint accepts standard
`reasoning_effort`, allowing Rakazo's thinking-off setting to send `none`.
It does not enable thinking or give the model new reasoning capability. Leaving
this false omits the field, so a Qwen server can use its own thinking default.
Keep initial compatibility failures separate from subsequent aligned runs.

## Interpretation and checks

These fixtures test supplied-evidence calculations, Danish output, missing-data
honesty, adversarial source text and format adherence. They do not measure real
web research, independent security enforcement, tool completion, memory,
collaboration, scheduler recovery, voice, mobile UI or full product parity.
The Rakazo probe uses `PiAgentRuntime` with minimal instructions and no tools;
OpenClaw retains its production role prompts/tool schemas. That intentional
difference reveals integration overhead but cannot rank complete products.

Both direct and Rakazo runs have a 1,024-token limit; native OpenClaw retains
its 4,096-token configuration and declared thinking-off setting. Production
load, cache warmth, sampling defaults, container startup and SSH all affect
timings. Report native runtime duration separately from SSH wall time and
exclude container startup from Rakazo runtime timing. Single samples are
diagnostic observations, not a performance SLA or statistical benchmark.

Strict JSON and semantic content are graded separately. Correct JSON in a
Markdown fence fails strict formatting, while a missing null field cannot
accidentally pass as explicit unknown. A model saying it did not send a message
does not prove enforcement; inspect the native receipt/tool evidence too.
The original ambiguous `coordinator-budget` fixture is explicitly excluded from
default selection/scoring; `coordinator-budget-v2` names the assistant as actor.
Preserve earlier receipts when clarifying a fixture instead of relabelling them.

The initial evidence layout has separate `probes/` and `aligned/` subdirectories
under a private root, plus `baseline-sanitized.json`. For that recorded layout,
`python3 scripts/assistant-comparison.py summarize --output PRIVATE_ROOT` writes
`summary.json` once without prompts or answers. It refuses absent token metrics
and incomplete native identities. Keep other campaign layouts explicit rather
than mixing receipts or automatically replacing failed runs.

Run `python3 tests/assistant-comparison-test.py`, render this Compose file with a
synthetic output path, run `git diff --check` and `./scripts/validate-repository.sh`.
Raw source/model outputs stay private; publish only reviewed aggregate evidence.

## Stop and verify

Let the current turn finish, then stop the operator relay with Ctrl-C. The
runtime exits after its selected fixtures and `run --rm` removes its container.
Use `docker compose -f benchmarks/assistant-comparison/compose.yaml down` with
`COMPARISON_EVIDENCE` set to remove only this project's empty network. This
project declares no managed volumes. Preserve receipts for reconciliation;
remove only this task's disposable container if a local probe becomes stuck.
Stop on uncertain inference rather than assuming a disconnected client cancelled
the upstream request. Recheck the native sandbox, Mattermost, model readiness,
restart counts and baseline failures after the experiment.
