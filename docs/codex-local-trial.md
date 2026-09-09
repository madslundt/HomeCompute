# Codex GB10 Local trial

## Operating decision

Cloud remains the default Codex mode for repositories explicitly classified
`cloud_allowed`. GB10 Local is an opt-in whole-session mode during the trial.
There is no per-message provider switching, automatic local implementation,
or silent fallback. A local session that cannot reach GB10 fails locally.

Repository privacy classification is explicit committed metadata at
`.codex/data-policy.json`:

```json
{
  "schema_version": 1,
  "classification": "cloud_allowed"
}
```

The only classifications are:

- `cloud_allowed`: an ordinary private or public repository may use an approved
  cloud model;
- `local_only`: repository content must remain local.

Missing, malformed, unknown, or non-regular metadata is treated as
`local_only`. The classification is never inferred from prompts or source code.
Only a deliberate reviewed commit may relax it. This repository is currently
classified `cloud_allowed`; there are no current software repositories known to
require `local_only`.

## Machine-local prerequisite

The repository does not install credentials or provider settings. Define the
documented provider in the user's Codex configuration and supply its key through
the environment:

```toml
[model_providers.gb10]
name = "GB10"
base_url = "https://ai.home.arpa/v1"
env_key = "GB10_AI_API_KEY"
wire_api = "responses"
requires_openai_auth = false
```

Inspect the selected mode without launching Codex:

```bash
python3 scripts/codex_session.py --dry-run
python3 scripts/codex_session.py --dry-run local
```

Start Cloud (the default) or GB10 Local explicitly:

```bash
python3 scripts/codex_session.py
python3 scripts/codex_session.py local
```

`codex_session.py` passes `model_provider="gb10"` and `model="coding"` only in
local mode. The choice therefore remains fixed for the entire process. Running
raw `codex` bypasses the repository classification check and is not the trial's
supported entry point.

## Trial procedure

Choose bounded, reversible work with objective verification. For every
representative task:

1. start a fresh GB10 Local session;
2. permit at most one evidence-informed local retry;
3. run the predetermined build/tests;
4. if needed, record that cloud reimplementation was required;
5. have a cloud session review the final diff and classify any serious defect;
6. append only outcome metadata to the private trial ledger.

Do not put prompts, source, diffs, paths, review prose, or private data in the
ledger. Use opaque task IDs. The recommended ledger path is
`benchmarks/results/codex-local-trials.jsonl`, which Git ignores and the tool
creates with mode `0600`.

The promotion gate requires at least 20 representative real tasks and at least
70% qualifying tasks. A qualifying task must complete without cloud
reimplementation, use no more than two local attempts, pass build/tests, and
pass cloud review without a serious defect. Synthetic and nonrepresentative
records do not enter the denominator.

The evaluator reports only `continue_explicit_trials` or
`eligible_for_consideration`. It always reports
`automatic_delegation_enabled: false`; crossing the evidence threshold does not
modify Codex, provider, or repository configuration. Automatic delegation
requires a later, explicit implementation and review decision.
