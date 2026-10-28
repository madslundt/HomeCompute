# Exact-plan speech startup diagnosis

Use this procedure when a candidate fails before its benchmark. It replaces no
benchmark or acceptance gate. The October 28 failed first replacement was
internally restored to healthy Hviske `.10`; Plapre remained `.06`. Its failed
container had already been removed and Docker's event ring started after the
incident, so its startup cause is unknown. The restored `.10` startup reported
3.87 GiB weights and 6.62 GiB available KV memory; those baseline numbers do not
establish that `.05` can start. The previous approved plan is stale after the
baseline container was recreated.

The owning source is `scripts/bootstrap-speech-startup.py` and
`scripts/diagnose-speech-startup.py`. The bootstrap takes only an exact published
source SHA, a private operator staging directory, and either `--plan` or an
explicit `--plan-sha256`. It fetches the fixed HomeCompute GitHub repository,
refuses dirty/mismatched existing root releases, compares its staged bytes with
published source, then executes only the protected helper with isolated Python.
It installs no packages and changes no sudo policy or release pointer. It reads
no stdin. The operator enters their normal sudo password once per command.

First publish/review the source, stage its exact bootstrap in a fresh user-owned
mode `0700` directory `/tmp/homecompute-speech-startup.RANDOM`, and independently
verify the published bootstrap SHA256. Run the **read-only** plan command (the
operator substitutes the concrete reviewed SHA and prepared staging path):

```bash
sudo python3 -I -B /tmp/homecompute-speech-startup.RANDOM/bootstrap.py \
  --revision FULL_PUBLISHED_SHA --plan \
  --result-directory /tmp/homecompute-speech-startup.RANDOM
```

The finite `startup-result.json` is root-owned mode `0644` inside that private
operator directory. The coordinator can read it over ordinary SSH. Review its
fresh plan: source SHA, actual container IDs, baseline/candidate hashes, and
budgets. This step does not stop ingress or restart engines. Never reuse the old
`0c36…` approval. With a fresh second staging directory/bootstrap and its reviewed
plan hash, the explicitly authorized startup probe is:

```bash
sudo python3 -I -B /tmp/homecompute-speech-startup.NEWRANDOM/bootstrap.py \
  --revision FULL_PUBLISHED_SHA --plan-sha256 FRESH_REVIEWED_PLAN_SHA256 \
  --result-directory /tmp/homecompute-speech-startup.NEWRANDOM
```

The helper rechecks the exact plan before maintenance and the qualifier checks
again under its capacity lock. It holds the rollout and Spark deployment locks,
uses the existing narrow ingress gate, stops only previously running speech
intake, and verifies model idleness before starting either candidate. Flash and
Whisper are unchanged. It performs no corpus, synthetic LLM load, or long speech
benchmark. Each Compose startup has a 900 second health wait and 960 second CLI
bound. It always restores the saved `.10/.06` baseline after a successful probe;
a failed replacement restores attempted services before returning. An outer
recovery finally handles partial/interrupted operations. Restore requires both
saved images/commands to be running and healthy before retiring `active.json`.
Uncertain restoration keeps that marker and reports incomplete/unverified.
The startup command exits successfully only after verified startup and baseline
restoration; a failed or unknown operation remains nonzero even after recovery.

Before rollback can remove a failed container, the qualifier writes an immutable
root-only `start-<nonce>.json` under its exact plan directory. It records the
source/plan and Compose hashes, service/profile, elapsed time, finite operation
exit code, exact image/command match booleans, numeric memory fields and fixed
startup categories. Host/cgroup OOM and explicit CUDA OOM are separate. Raw
Docker output, commands, environment, model logs, audio and prompts are never
persisted or relayed. Read-only capture consumes at most 4 MiB, keeps a bounded
suffix in memory, and kills/reaps only its own Docker CLI on limit/timeout.
Compose operations likewise have bounded separate stdout/stderr; truncated JSON
is refused. Unavailable or ambiguous evidence remains unknown.

`attempt-result.json` binds the exact plan/source to the immutable start-receipt
hashes and verified rollback outcome. Its SHA and finite outcome travel in the
public result. Normal rollout reporting now recognizes this exact-plan internal
rollback as `baseline-restored` / `restored`, even if the qualifier has already
retired its active marker. A receipt for another plan cannot make that claim.

Every diagnostic result retains `capacity_qualified=false`, `accepted=false`
and `full_voice_accepted=false`. A successful startup probe requires a separate
fresh full capacity trial before any capacity promotion. Do not change canonical
budgets, delete a recovery marker, or repeat a full benchmark to discover an
uncaptured startup error.
