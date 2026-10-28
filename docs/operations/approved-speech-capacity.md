# Explicitly approved speech capacity experiment

This controller replaces the broken interactive approval input for exactly the
reviewed plan `0c36bc423531730ae23bec789c2c06896376f0500028b4c6309dbed0cb75d0dd`.
It runs the qualification implementation at the fixed published release
`9c63ffab299e7325419a4515cb8acb3b4d5b7dce`. The model, corpus, concurrency,
controlled load, capacity/quality/latency gates, signal cleanup and rollback all
remain owned by that release. A fresh baseline and candidate are still required.

The new controller's SHA identifies only the input adapter. Both releases must
be clean root-owned immutable checkouts from the fixed public repository
`https://github.com/madslundt/HomeCompute.git`, with exact HEADs and ancestry on
`origin/master`. The qualification ROOT and its sibling imports stay at the old
release, preserving source/build paths and the reviewed plan hash. No later
controller code is substituted into the qualification implementation.

The wrapper verifies the fresh preflight plan before measuring the baseline,
then supplies a callback that accepts only the exact reviewed hash. The pinned
trial independently regenerates and compares its plan after the baseline. A
changed plan at either boundary stops before any model replacement. No stdin
approval, piped `APPLY`, ambient approval setting or generic autoapproval is used.
Another plan needs a new explicit review and controller source revision.

Stage the reviewed `scripts/bootstrap-approved-speech-capacity.py` and the same
external pinned public `corpus.tar` in a fresh operator-owned `0700` directory
`/tmp/homecompute-spark-rollout.XXXXXXXX` on Spark. Verify the bootstrap SHA-256
against the new published controller commit. The old corpus pin remains
14,848,000 bytes and SHA-256
`d673313799df319cf26873981d017458b58e91cc70320f1d66c3b59c405af06b`.

After substituting the reviewed full controller SHA and fresh staging path,
the human runs one command and enters the normal sudo password once:

```bash
ssh -t home-spark 'sudo python3 -I -B /tmp/homecompute-spark-rollout.XXXXXXXX/bootstrap-approved-speech-capacity.py --revision FULL_CONTROLLER_SHA --approve-plan-sha256 0c36bc423531730ae23bec789c2c06896376f0500028b4c6309dbed0cb75d0dd --corpus-archive /tmp/homecompute-spark-rollout.XXXXXXXX/corpus.tar'
```

The self-contained bootstrap imports no staging modules. It materializes and
verifies both fixed-repository releases, compares its own staged bytes with the
published source, then executes the controller with Python isolation enabled.
Existing dirty/mismatched releases and existing result paths are refused.
No packages, sudo policy, mutable checkout, general release pointers, privileged
socket or remote control service are changed.

The original root-private receipts remain under
`/var/lib/homecompute-spark-rollout/trial-*`. The operator's same private staging
directory receives the original finite root-owned `0644` `rollout-result.json`.
Its `source_revision` remains the fixed qualification SHA. The controller prints
its distinct revision and reviewed plan hash before handing control to the old
main function. Credentials, env values, prompts, transcripts and audio are not
copied into these public metadata reports.

On failure or interruption, the pinned trial/main perform their existing
baseline rollback and restore only previously running ingress. A passing
capacity result still reports `accepted: false`, `full_voice_accepted: false`
and reconciliation required. This command does not finalize production env
budgets, retire the active capacity override, promote voice readiness or change
Flash. Canonical finalization requires its separate reviewed lifecycle after
actual measurement succeeds.

Validation: `python3 tests/approved-speech-capacity-test.py` exercises exact-plan
approval with EOF stdin, pre/post-baseline changes, failed/interrupted candidate
rollback, pinned main arguments/imports and immutable release refusal.
