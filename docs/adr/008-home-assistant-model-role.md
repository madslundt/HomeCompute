# ADR-008: Separate Home Assistant logical role

## Context

Smart-home requests prioritize latency, predictable tool/entity selection,
short speech responses, and safe failure. Those priorities differ from n8n
summarization and coding.

## Decision

Expose `home` as a separately qualified logical role. Attempt native
deterministic Assist intents first. Use Home Assistant's LLM API as the sole
authority for exposed tools and execution. A shared physical text model is
allowed only if it passes the home benchmark under mixed load.

## Alternatives

- Always use `automation`: rejected without role-specific evidence.
- Route every command through an LLM: rejected.
- Give the model direct device/MQTT/Zigbee access: rejected.

## Consequences

Entity-count, denial, dangerous-action, Danish, and mixed-load tests are
mandatory. A smaller or reserved model/process may be selected if required by
latency or isolation measurements.

## Status

Accepted; concrete model pending benchmark.

## Evidence

- `docs/research/home-assistant-model-evaluation.md`
- `docs/requirements.md` URS-HA and URS-PERF requirements
