# Model routing refactor baseline

**Observed:** 2026-09-27
**Repository commit:** `fbc523cf0612f99f8dd75c9925fe681c2c1d8ea5`
**Branch:** `master`, matching `origin/master` before edits

This baseline was gathered read-only before editing route or consumer
configuration. Live values below describe the observations at this date; they
are not proof of a later deployment state.

## Repository validation

`./scripts/validate-repository.sh` passed on the unmodified commit, including
shell checks, model and routing tests, Compose rendering, YAML/JSON validation,
Nix flake evaluation, and D2 rendering.

## Gateway and model visibility

- The configured and running LiteLLM version is `1.99.1`. The control-plane
  image digest is pinned in `config/control-plane.env.example`.
- `home-core` SSH inspection showed Caddy, LiteLLM, PostgreSQL, n8n, Aula MCP,
  Tilbudstrolden MCP, and the speech relays healthy.
- The three available client credentials
  (`HOMECOMPUTE_PI_API_KEY`, `HOMECOMPUTE_API_KEY`, and
  `HOMECOMPUTE_SCRIPT_API_KEY`) each returned exactly `automation-moe` from
  authenticated `/v1/models`. The credential values were not printed.
- Administrative and Hermes canary credentials were not available in this
  workstation session, so their authenticated `/v1/models` results were not
  repeated. The previous live evidence is described in
  `docs/control-plane-deployment.md` and the Hermes readiness audit.
- Read-only n8n MCP inspection found active `Shopping list`, `Aula calendar
  sync`, and `Notion AI automations` workflows with the `HomeCompute LiteLLM
  OpenAI` credential and `model=automation-moe`. Their model nodes set
  `timeout=600000` ms and `maxRetries=0`. Active `Sub-Workflow: Aula Collector
  & Analyze` exposed a Gemini model node in its current workflow graph; it did
  not expose the HomeCompute node in the returned graph. `Aula - Family
  Briefings` is active and has no model node matching the inspected node-type
  set. Historical workflow inventories therefore need a fresh execution-path
  audit before every workflow is migrated.
- The isolated Danish response and tool-call checks documented in the prior
  inventory passed. Production workflow executions were not part of this
  baseline.

## Compute and fallback

- `home-core` reported 46 GiB total RAM, 11 GiB used, and 35 GiB available;
  swap is disabled. Docker showed the automation-backup container stopped.
- The cold fallback model is documented as a 22,134,528,992-byte Qwen3.6 Q4
  GGUF with a 28 GiB container memory limit. Its startup/load time and resident
  memory were not measured in this run because it is intentionally stopped.
- The agents VM is disabled pending off-host backup. Its proposed 16 GiB budget
  and the standby's 28 GiB limit make concurrent activation a capacity gate.
- `home-spark` reported 121 GiB total RAM, 82 GiB used, 13 GiB free, 39 GiB
  available, 15 GiB swap total, and 4 GiB swap used. `sudo docker ps` was not
  available to the SSH account, so current running containers were not
  re-enumerated. The 2026-09-26 live inventory records Qwen3.6 automation and
  Gemma Home Assistant active, with Qwen3.8 stopped.
- The 2026-09-26 live inventory records the `assistant-canary` alias on the
  Qwen3.6 deployment. This run could not query it with its separately scoped
  credential.

## Rollback

Before generating the new model section, the prior source config was copied to
`deploy/control-plane/litellm-config-pre-registry-rollback.yaml`. The separate
`litellm-config-automation-backup.yaml` remains the operator-controlled cold
maintenance configuration. A source rollback consists of restoring the first
file to `litellm-config.yaml`, validating with `docker compose config --quiet`
and the repository validation script, then applying the reviewed deployment
commit. No deployment, LiteLLM restart, virtual-key edit, or n8n workflow edit
was performed during this change.

## Remaining baseline gaps

The following are intentionally not inferred from stale documents or source
configuration: admin-key and Hermes-key model listings, Spark container status
from the host, CPU standby startup/load time, standby loaded memory, postgresql
and LiteLLM mixed-load memory, and production n8n execution timing/recovery.
Those observations are required before promotion or hot-standby decisions.
