# ADR-024: Separate model artifacts, deployments, and capability routes

**Status:** Accepted for staged migration; production promotion remains gated
**Date:** 2026-09-27
**Supersedes:** the duplicated alias/model inventory in `model-router-policy.json`; it does not supersede the hardware and speech qualification evidence in ADR-019 or ADR-021.

## Context

The former model-router policy recorded aliases, model identities, client
permissions, selection lanes, and a disabled `auto` classifier in a second
inventory. LiteLLM separately carried live deployment names and routes, while
compute setup scripts embedded runtime and model details. This made model
replacement require unrelated consumer, gateway, and setup-script edits.

The live configuration also advertised aliases backed by a Qwen3.8 service
that was documented as intentionally stopped. n8n used `automation-moe` while
the configured CPU standby belonged only to `automation`; that standby is cold
and cannot be presented as request-path automatic failover.

## Decision

Keep four distinct identities:

1. **Model artifact** — upstream ID, immutable revisions, license,
   quantization, capabilities, context bounds, runtime compatibility, and
   qualification evidence.
2. **Deployment** — artifact reference, runtime profile, host role, endpoint
   environment variable, backend-served name, context, concurrency, lifecycle,
   and availability.
3. **Capability route** — stable consumer alias, local-only privacy class,
   required capabilities/protocols/context, timeout profile, and ordered
   qualified deployments.
4. **Consumer authorization** — LiteLLM virtual-key model scope, managed
   independently from route availability and backend tool support.

`config/model-catalog.json` and `config/capability-routes.json` are the
canonical source for text routes. `scripts/model_registry.py` validates their
references and deterministically renders only LiteLLM's `model_list`. Security,
transport, secret handling, and virtual-key policy stay explicit and are not
generated from model metadata. Disabled and intentionally stopped backends are
omitted from active capability routes. Candidate aliases are explicit and
remain separately authorized.

Compute services expose internal deployment names. They do not expose a list
of public capability aliases. A model or deployment addition cannot grant
existing clients access because route generation does not change virtual keys.
There is no automatic prompt classifier or cloud fallback.

Timeout profiles initially set 20 seconds for `home`, 120 seconds for
`automation` and the assistant canary, and 600 seconds for long-running
workloads. These are operational starting budgets, not final measured SLOs.
The request timeout is emitted on each LiteLLM deployment; live synthetic
failure tests and p95/p99 observations remain promotion gates.

## Consequences and migration gates

- The public API can remain stable when the artifact behind a qualified
  deployment changes.
- Cold standby remains an operator lifecycle. It is excluded from ordinary
  request failover until it is measured, hot, and capability-qualified.
- The checked-in prior LiteLLM config is retained for immediate source rollback.
- n8n still has to migrate its separately scoped key and workflows from
  `automation-moe` to `automation` in a controlled sequence. The checked-in
  change does not update the live gateway or n8n.
- Assistant remains canary-only until Hermes streaming, tool, context, outage,
  mixed-load, OpenShell, and backup/restore checks pass.
- The GB10 provisioning script and modality roster remain a later refactor;
  their existing host, firewall, cache, and rollback safeguards must be kept.

## Evidence

- `docs/model-routing-refactor-baseline-2026-09-27.md`
- `config/model-catalog.json`
- `config/capability-routes.json`
- `scripts/model_registry.py`
- `tests/model-registry-test.py`
- `docs/current-state.md`
