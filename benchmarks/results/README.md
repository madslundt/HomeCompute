# Private automation benchmark results

Raw benchmark runs remain local and ignored by Git. Store them in a dedicated
directory with mode `0700`; the harness defaults to `benchmarks/results/` and
creates private result files. Do not commit prompts, messages, tool payloads,
execution exports, credentials, or generated household data.

Only sanitized aggregate records conforming to [schema.json](schema.json) may
be shared. The example is synthetic and contains no model output or personal
data. The report at `docs/benchmarks/qwen36-vs-flash-next-automation.md` is the
canonical promotion status.
