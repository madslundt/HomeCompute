# Model benchmark harness

This directory implements the Phase D fixture contract described in the
implementation plan. It supports repeatable text-model screening through
OpenRouter, direct OpenAI-compatible local endpoints, objective assertions,
anonymous rubric judging, and deterministic reports.

The harness uses only the Python standard library. Raw results are written
under `benchmarks/results/`, which is ignored because prompts and responses may
contain sensitive data. Commit only synthetic fixtures and explicitly sanitized
summary records under `docs/benchmarks/`.

## Explicit GB10 Codex trial

The initial developer workflow deliberately has two whole-session modes. Cloud
is the default only for a repository whose committed `.codex/data-policy.json`
sets `classification` to `cloud_allowed`. Missing metadata fails closed to
`local_only`. Start a session through the policy-aware entry point:

```bash
python3 scripts/codex_session.py                 # Cloud, when allowed
python3 scripts/codex_session.py local           # GB10 Local for the whole session
python3 scripts/codex_session.py local -- --no-alt-screen
```

The local command selects the user-configured `gb10` provider and logical
`coding` model. It does not install provider configuration or read a secret.
The required user-level provider configuration is documented in
`docs/codex-local-trial.md`. Automatic cloud-plan/local-build delegation remains
disabled.

After a real local task and its cloud diff review, append metadata-only evidence
to a private ledger under the ignored `benchmarks/results/` directory:

```bash
python3 benchmarks/codex_trial.py record \
  --ledger benchmarks/results/codex-local-trials.jsonl \
  --task-id HC-001 --representative yes --outcome completed \
  --local-attempts 1 --verification passed \
  --cloud-reimplementation no --cloud-review passed \
  --duration-minutes 12 --model coding --runtime qwen3.8-27b-nvfp4-vllm \
  --input-tokens 12000 --output-tokens 2400 \
  --throughput-tokens-per-second 42.5

python3 benchmarks/codex_trial.py status \
  --ledger benchmarks/results/codex-local-trials.jsonl
```

A task qualifies only when it completes locally with passing build/tests, at
most two local attempts, no cloud reimplementation, and a passing cloud review
with no serious defect. The status becomes `eligible_for_consideration` after
at least 20 representative tasks and a qualifying rate of at least 70%. This is
evidence for a human decision and never activates automatic delegation.

## Start with the synthetic smoke suite

```bash
python3 benchmarks/harness.py validate \
  --plan benchmarks/plans/smoke.json \
  --release benchmarks/manifests/release.example.json

python3 benchmarks/harness.py run \
  --plan benchmarks/plans/smoke.json \
  --release benchmarks/manifests/release.example.json
```

The second command prints the new result directory. It contains:

- `run.json`: immutable run identity and the private anonymous-label map;
- `generations.jsonl`: raw responses, objective checks, timings, and usage;
- `summary.json`: deterministic aggregate data;
- `summary.md`: operator-readable report.

Result directories are private (`0700`) and retained artifacts are written
atomically with mode `0600`. Existing non-empty directories, symbolic links,
special files, and unsafe pre-existing artifacts are rejected so prior evidence
cannot be overwritten or redirected.

## Add an OpenRouter candidate

Copy the disabled candidate in `plans/smoke.json`, use an exact model slug, and
add a matching entry to the release manifest. OpenRouter candidates must pin
`provider.only` and set `provider.allow_fallbacks` to `false`; validation rejects
ambiguous provider routing. Keep keys out of JSON:

```bash
export OPENROUTER_API_KEY='...'
```

For local GB10 validation, use adapter `openai_compatible` and set `base_url` to
the direct vLLM test listener. The release artifact must identify the exact
weights, revision, quantization, runtime, template, and relevant runtime flags.

Before invoking any non-OpenRouter HTTP or Codex candidate, approve its exact
origin outside the plan. This prevents a changed benchmark file from redirecting
an API key to a new host:

```bash
export HOMECOMPUTE_BENCHMARK_ALLOWED_ORIGINS=https://ai.home.arpa,http://10.77.10.10:8000,http://home-core:15678
```

Origins are comma-separated `scheme://host[:port]` values. OpenRouter is fixed
to `https://openrouter.ai`. Redirect responses are rejected rather than followed
with an authorization header.

## Run blinded judging

Add and enable a judge in the plan, then run:

```bash
python3 benchmarks/harness.py judge \
  --plan benchmarks/plans/smoke.json \
  --run benchmarks/results/RUN_DIRECTORY \
  --judge frontier-judge
```

The judge receives anonymous labels, the task, the rubric, the response, and
objective results. It does not receive the candidate map. Before judging, the
supplied plan and case fixtures must exactly match the retained run snapshots.
Scores must use every rubric ID and range from 0 to 4. Re-running resumes rather
than duplicating completed judgments; transient failures remain retryable and
are recorded separately in `judgment-attempts.jsonl`. Aggregation remains
deterministic Python code; the judge does not choose the winner.

## Rank a completed run

After every enabled judge has produced a judgment for every completed,
rubric-bearing trial, rank the retained run evidence with explicit thresholds:

```bash
python3 benchmarks/harness.py select \
  --run benchmarks/results/RUN_DIRECTORY \
  --minimum-quality 85 \
  --minimum-objective-pass-rate 100 \
  --output benchmarks/results/RUN_DIRECTORY/selection.json
```

`selection.json` is schema-versioned evidence, not production qualification or
an instruction to download, activate, or promote a model. The harness verifies
the retained plan, release, case, candidate, trial, and judgment identities
before ranking; evidence must cover every enabled candidate. Every candidate,
ranking, and winner records immutable artifact provenance as `artifact` with
`artifact_ref`, `source`, `revision`, `runtime`, `quantization`, and the
canonical JSON `artifact_sha256` of its retained release artifact. Selection
fails closed unless each enabled candidate's `artifact_ref` identifies exactly
one release artifact. Candidates with incomplete trials or judgments, fatal
judgments, or failed objective checks are ineligible. Eligible candidates sort
by normalized quality, objective pass rate, median duration, then stable
candidate ID. Thresholds are percentages from 0 through 100 and are never
inferred. If no candidate clears all safety rules and both thresholds, `outcome` is
`no_eligible_winner`, `rankings` is empty, and `winner` is `null`.
The optional `--output` form refuses to overwrite an existing selection; omit
it only when JSON on standard output is intentionally ephemeral. Use a new
destination when retaining selections made with different thresholds. Keep
selection evidence with the ignored raw run unless it has been explicitly
sanitized.

If review will use an interactive Codex subscription rather than an API judge,
create a packet that excludes the private candidate map:

```bash
python3 benchmarks/harness.py review-packet \
  --plan benchmarks/plans/smoke.json \
  --run benchmarks/results/RUN_DIRECTORY
```

Open `review-packet.json` in a new Codex task and request rubric scoring before
opening `run.json`, which contains the model identities.

See [REVIEWING.md](REVIEWING.md) for the manual scorecard, fatal-error rules,
and the ready-to-use blinded Codex reviewer prompt.

## Fixture preparation

Each case is one synthetic or sanitized JSON file with:

- a stable ID and track;
- the exact messages sent to all candidates;
- objective checks with weights;
- optional subjective rubric items with weights.

Supported objective checks are `contains`, `not_contains`, `regex`,
`valid_json`, `json_path_equals`, `file_exists`, `file_contains`, and `command`.
Validation rejects malformed check objects, invalid type-specific fields,
non-positive or non-finite weights, and invalid command exit or timeout bounds.
Fixture, workspace, overlay, and file-check paths cannot escape their benchmark
or disposable-workspace roots. Symbolic links and special files are rejected
recursively before copying or evaluation.

Command checks use argument arrays without a shell and run only inside the
disposable workspace through a managed Codex sandbox. The sandbox grants
minimal read access, grants write access only to that workspace, restricts
network access, and uses a minimal environment. Command checks fail closed when
Codex sandbox support is unavailable. Prefer objective checks for business
rules and reserve rubric judging for qualities that cannot be asserted
mechanically.

The `codex_exec` adapter runs implementation candidates through an ephemeral
Codex CLI session against a fresh copied workspace, retains its patch and event
log, runs fixed checks, then deletes the copy. The Codex supervisor itself runs
inside a managed outer sandbox with minimal read access, workspace-only writes,
an isolated private runtime environment, and provider network access. The inner
`codex exec` workspace sandbox still restricts network access for model-generated
commands. It uses a custom Responses provider and never uses the reviewer's
Codex subscription for candidate work.

The ready code paths are:

```bash
# Direct code-understanding screening with pinned OpenRouter providers
python3 benchmarks/harness.py run \
  --plan benchmarks/plans/code-understanding-openrouter.example.json \
  --release benchmarks/manifests/openrouter-direct.example.json

# Agentic implementation in disposable workspaces through Codex
python3 benchmarks/harness.py run \
  --plan benchmarks/plans/code-openrouter.example.json \
  --release benchmarks/manifests/code-openrouter.example.json
```

The `n8n_webhook` adapter can call the repository's isolated synthetic
[n8n model benchmark](../automations/model-benchmark/README.md). Do not point it
at production workflows.

Use [PREPARATION.md](PREPARATION.md) as the collection checklist for real coding
and automation fixtures.
