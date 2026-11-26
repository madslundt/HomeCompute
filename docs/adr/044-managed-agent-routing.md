# ADR-044: Managed text routes in LiteLLM

**Status:** Proposed for deployment after pinned-runtime and consumer validation

**Date:** 2026-11-24

[ADR-024](024-model-artifact-deployment-capability-separation.md) separates
artifacts, deployments, and capability aliases. [ADR-035](035-gateway-owned-chatgpt-subscription.md)
establishes the shared ChatGPT OAuth identity. The existing direct aliases
cannot change providers automatically, so a failure requires each client to
choose another model.

Introduce named role aliases with ordered LiteLLM deployments. Use the
capability registry and generated model list to track their primary, fallback,
privacy, timeouts, and effort as one reviewed change. Cloud-enabled roles
require a key with an explicit route grant. The legacy keyless catalog under
[ADR-040](040-keyless-lan-model-catalog.md) keeps the direct aliases and
excludes the cloud-enabled role aliases. Private routes are restricted to
declared local endpoints.

The native Router owns infrastructure fallback within a model group. The request
boundary enforces portable text/function requests and a total deadline.
Applications retain authority over tools, conversation state, and quality
escalation. [ADR-010](010-cloud-fallback.md) continues to cover orchestration
for implementation quality. The default local-only workflows keep their
current cloud and action boundaries.

The existing OAuth models share an upstream. An independent cloud deployment
requires an approved credential, capability tests, and a cost policy. A cold
local standby requires explicit promotion before joining an automatic chain.

See the [managed routing runbook](../../deploy/control-plane/agent-routing.md)
for configuration, rollout and recovery.
