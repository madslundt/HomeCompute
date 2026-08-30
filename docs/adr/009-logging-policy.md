# ADR-009: Metadata-only logging

## Context

Inference requests may contain source code, home state, audio, tool data, and
sensitive content from existing n8n workflows, including Aula-derived content.
Body logging is not required for routine operations.

## Decision

Disable prompt, response, audio, transcript, tool argument/result, authorization,
and body logging by default in every layer. Record bounded operational metadata
only. Debug content logging requires an explicit time-bounded procedure with
nonsensitive fixtures; it is never enabled on live Aula/n8n or home traffic.

## Alternatives

- Full body logs for troubleshooting: rejected.
- Hash prompt bodies: rejected as unnecessary and potentially correlatable.
- No logs at all: rejected because availability/performance/fallback need evidence.

## Consequences

Troubleshooting relies on request IDs, error classes, metrics, and reproducible
synthetic cases. Automated log canaries and retention/access controls become
acceptance requirements.

## Status

Accepted.

## Evidence

- `docs/requirements.md` URS-SEC-006/007 and URS-OPS-006
- `docs/verification-strategy.md` V-SEC-006/008

