# Finalize a measured speech capacity candidate

This targeted lifecycle makes a successfully measured `.05/.05` capacity
candidate reproducible through canonical source and the two production env
files. It does not grant voice acceptance. The required qualification result is
`capacity_qualified: true`, `accepted: false`, `full_voice_accepted: false` from
qualification release `9c63ffab299e7325419a4515cb8acb3b4d5b7dce`.

Do not change or publish canonical budgets until the real trial passes. The
current `.10/.06` source is deliberately refused by this finalizer.

## Publish the canonical operating point

After the actual successful trial, review a new published full SHA containing:

- `.05` in both `config/hviske-stt.env.example` and
  `config/plapre-tts.env.example`;
- `.05` as the effective Hviske Compose budget fallback;
- normal setup validation accepting exactly the measured `.05/.05` tuple;
- corresponding validation, tests and documentation for those budget changes;
- these finalization scripts, with all qualified model/image pins, context,
  concurrency, voice, tempo, authentication, limits and network settings intact.

Keep the historical qualification implementation intact. Its private baseline
and candidate documents still describe the `.10/.06` versus `.05/.05`
experiment. A normal setup must retain the new `.05/.05` operating point after
finalization; it must not silently reset a model to its old budget.

The finalizer verifies both clean, protected Git releases from the fixed public
repository. It re-evaluates the private baseline/candidate gates, continuous
headroom summary, pinned public corpus and both controlled-load receipts. It
checks the reviewed result's SHA, active plan SHA and private document hashes.
It then verifies the actual qualified images, commands, environment contract,
mounts, networks, resource bounds, health and candidate-file ownership.

The new canonical selected-service definitions must equal the saved candidate,
including build args and referenced resources. Only the build context's release
ownership may differ. The owning setup validators must accept private projected
env copies; the effective Hviske fallback is also checked through Compose.
No real production env file changes during these preflights.

## One human command

The coordinator stages the reviewed public
`scripts/bootstrap-speech-finalization.py` in a fresh operator-owned `0700`
directory `/tmp/homecompute-speech-finalization.XXXXXXXX` and verifies its hash
against the new published canonical commit. No corpus upload is needed again.

Use the trial basename from the successful rollout's private receipt path.
`QUALIFICATION_RESULT_SHA256` is the `private_receipt.sha256` in that rollout's
finite public report: it hashes the private trial `result.json`, rather than the
public `rollout-result.json` or the plan object.

After substituting those exact reviewed values, the human runs:

```bash
ssh -t home-spark 'sudo python3 -I -B /tmp/homecompute-speech-finalization.XXXXXXXX/bootstrap-speech-finalization.py --revision FULL_CANONICAL_SHA --trial trial-EXACT_BASENAME --qualification-sha256 QUALIFICATION_RESULT_SHA256'
```

Sudo asks for its normal password once. The self-contained bootstrap imports no
staging modules, materializes/verifies the selected canonical release and the
fixed old qualification release, and executes only the published finalizer. It
installs no packages, changes no sudo policy or general release pointer, and
adds no privileged remote interface. Existing dirty or mismatched releases are
refused.

## Transaction and retirement

The finalizer holds the Spark deployment, rollout and capacity locks. A private
write-ahead journal and exact env snapshots live below
`/var/lib/homecompute-speech-finalization`.

Two different env files cannot be renamed atomically as a pair. Each file is
replaced atomically, preserving its owner/group/mode and every byte except its
single budget value. The journal and compare-before-write guards make a
partial update recoverable. An outside env edit is preserved and reported for
reconciliation.

Maintenance drops only NEW connections to the two speech model IPs/ports in a
private nonce-named Docker forwarding chain. Existing requests finish. Known
model ownership/contract, TCP activity and Hviske queue/running counters must be
quiet for three samples, within 120 seconds. A known partial-start model with
no API listener has no fabricated metrics; stopped/absent models are handled
explicitly during recovery. Only previously running Hviske/Plapre HTTP ingress
is paused and resumed. Other models, CPU fallbacks and retired Wyoming services
are outside this lifecycle.

Only the two canonical model services are force-recreated, with
`--no-deps --no-build --pull never`. Recreation is required to move Compose
ownership labels from private candidate JSON to the new immutable source and
production env paths. Both engines receive a fresh identity/health/env check
after both starts; an earlier successful check is insufficient.

The active capacity override is archived only after those checks. Intake
cleanup must then succeed before the transaction is complete. No benchmark,
plan, env values, prompts, transcripts or audio are copied into public reports.

The fresh staging directory receives root-owned `0644`
`finalization-result.json`, with the canonical and qualification identities,
target budgets, override state and private journal path/hash. A successful
report has `state: canonical-capacity`, `override_active: false`, and both
acceptance flags false. Verify the actual model labels/images/budgets and the
core authenticated voice path separately. The general Spark release marker is
not changed by this dedicated lifecycle.

## Failure and recovery

Failure or SIGINT/SIGTERM/SIGHUP attempts to restore the original env files and
the qualified candidate from its exact old private Compose documents. Healthy
unchanged candidate processes are preserved. Partial model restoration tries
both models; the active override remains/restores, and cleanup resumes only the
original ingress set. Failed cleanup, outside edits, unavailable Docker state
or a busy recovery window are reported as incomplete; active requests are never
force-killed to manufacture success.

SIGKILL and power loss cannot run cleanup. A surviving journal is refused by a
normal retry until recovery. Stage a fresh reviewed bootstrap directory and add
`--recover` to the same command, using the same canonical SHA, trial basename
and result hash. Recovery validates the journal, removes only its exact known
restrictive gate, drains the known models and restores the qualified candidate
and original env. Unknown gate rules, references, env ownership or hashes are
refused. It does not overwrite another active override.

After successful recovery, a normal retry keeps the prior attempt's journal
and obtains a fresh nonce. A completed replay verifies the canonical operating
point without restarting models. A missing or malformed public result remains
unverified; retain the private journal and its backups until reconciliation.

Absolute ASR/TTS latency, the complete Home Assistant voice path and fallback
acceptance remain separate gates even after canonical capacity finalization.

## Source validation

```bash
python3 tests/speech-finalization-test.py
git diff --check
./scripts/validate-repository.sh
```

The focused tests use synthetic private receipts and disposable local files,
with model and host commands mocked. They do not prove live capacity or perform
production model restarts. Real target evidence comes from the original trial
and the separately authorized finalization run.
