# Managed LiteLLM text routes

Consumers select a stable alias on the existing OpenAI-compatible gateway.
The [capability registry](../../config/capability-routes.json) defines the
ordered deployments, reasoning effort, request budget, and key profiles.
The [model catalog](../../config/model-catalog.json) owns local endpoints and
artifact details. Route changes preserve model names at the client.

Browse the [AI Hub](https://llm.home.arpa/ui/model-hub-table/) or the
**LiteLLM Models** card on Homepage for one entry per alias. The signed-in
AI Hub uses `/model_group/info`. **Models + Endpoints** lists each backend
deployment separately, including the primary and fallbacks that share an alias.

| Alias | Primary | Second | Third | Gateway deadline |
| --- | --- | --- | --- | --- |
| `agent-assistant` | Flash-Next | Luna High | Sol High | 180 s |
| `agent-coder` | Flash-Next | Sol High | Astra High | 240 s |
| `agent-planner` | Sol High | Astra High | | 210 s |
| `agent-reviewer` | Sol High | Astra High | | 210 s |
| `agent-researcher` | Sol High | Astra High | | 210 s |
| `agent-designer` | Astra High | Sol High | | 210 s |
| `agent-reasoner` | Sol High | Astra High | | 210 s |
| `agent-automation` | Flash-Next | Luna High | Sol High | 180 s |
| `agent-fast` | Luna Low | Sol Low | | 75 s |
| `agent-premium` | Astra High | Sol High | | 210 s |
| `local-only` | Flash-Next | | | 90 s |
| `local-first` | Flash-Next | Luna High | Sol High | 180 s |
| `cloud-first` | Sol High | Luna High | | 150 s |

The Flash-Next assignment uses the UltraFast runtime observed on Spark, the
existing relay, and the runtime's default effort.
Sol, Luna, and Astra use the existing **gateway-owned ChatGPT OAuth** login.
The legacy aliases `automation-moe`, `chatgpt-sol-latest`,
`chatgpt-luna-latest`, `chatgpt-astra-latest`, and `chatgpt-terra-latest`
retain their direct selections and existing client parameters.

## Reliability and privacy

Managed aliases accept stateless text, client-executed function tools, and
structured-output fields on Chat Completions and Responses. Structured requests
only reach deployments marked qualified in the registry. Live OAuth tests
returned valid JSON when instructed but failed an enforced-schema test, so
constrained requests currently stay on Flash-Next or fail explicitly when
the chain has no qualified deployment. Resend portable
message and tool history on each turn. Provider-managed conversation IDs,
encrypted reasoning items, media, remote tools, and routing override fields are
rejected. The gateway controls effort on each attempt and ignores the client's
`reasoning_effort` or `reasoning` on managed aliases.

The router uses deployment `order` within a single alias. It allows up to three
total provider attempts across the chain, with zero same-deployment or SDK
retries. Failed deployments in a chain enter a 60-second cooldown according to
LiteLLM's failure policy, after which primary selection resumes. This keeps a
connection failure or rate limit from multiplying agent and SDK retries. The
per-deployment request and stream timeouts in the registry sit inside an
absolute gateway deadline. Treat the gateway as the sole automatic retry
owner. See the [LiteLLM Router reliability reference](https://docs.litellm.ai/docs/router/learn/reliability).
Choose client timeouts above the route deadline and disable client
retries for streamed or side-effecting turns. If a stream breaks after content
or tool output, the client receives a failure or an incomplete stream and
must reconcile any tool execution before retrying.

Every deployment behind `local-only` must use a declared private IP endpoint.
It has no fallback currently. The local CPU backup is cold and still requires
its existing explicit lifecycle. Managed routing rejects external/global
fallback configuration at startup. The gateway rejects request-level routing
overrides and only dispatches deployments attached to the authenticated alias.

The remaining aliases permit cloud on the **first** or a later attempt. Use
them only for prompts, tool results, and working directories approved for
OpenAI. Each cloud route requires an explicit named grant on a client key.
The LAN inference key retains the legacy direct catalog and `local-only`.
The gateway filters new cloud-enabled roles from the keyless listing. API and
MCP grants remain independent.

## Enroll a consumer

The registry declares scopes for Hermes assistant, OpenBot specialists, coding
workflows, public-input n8n, and private inference. Each instance gets its own
virtual key. Keep it in the application's credential store. The setup script
uses a root-owned mode 0600 `sk-…` file supplied by the operator at
`/srv/state/control-plane/agent-routing-keys/agent-routing-PROFILE-INSTANCE`.
The parent directory should be root-owned mode 0700. Run setup from the
published release on Home Core. This grants the named inference routes with
the declared request and token quotas. It gives the key no MCP privileges.

```bash
sudo python3 scripts/setup-agent-routing-key.py coding-workflow test-client --apply
sudo python3 scripts/setup-agent-routing-key.py coding-workflow test-client
```

Omit `--apply` for verification. Rotate a key to change its scope or quotas.
Revoke the retired virtual key through LiteLLM and remove the managed file
after confirming the replacement. An administrator can manage credentials
through LiteLLM. Keep deployment policy in Git because model storage in the
database is disabled.

| Consumer | Selection and migration |
| --- | --- |
| OpenCode on Home Core | The checked-in default and small model use `local-only`. The rollout reconciles its existing model key and keeps its MCP grant and manual `automation-moe` selection. Existing sessions can retain their chosen model. |
| Hermes | Use `agent-assistant` in an explicitly enrolled stateless Chat profile approved for cloud prompts and tool results. The owner profile, Home Assistant voice, and memory pipeline keep their existing bindings. Hermes profiles that send extra provider-specific fields need a compatibility test before enrolment. |
| OpenBot | In an enrolled synthetic or approved specialist provider, choose the role alias for planning, review, research, design, or reasoning. Keep existing agent IDs, model definitions, keyless provider settings, and household/private bindings. Check that the model client sends the scoped credential and preserves function-call or schema output. |
| Paseo | The [Paseo workspace](../paseo/README.md) uses a dedicated inference-only credential. The team and voice defaults use local routes. Cloud-backed aliases are selectable for owner-approved work. |
| n8n | Use `agent-automation` only in a separately approved public-input workflow with its own credential. The existing private-data workflows, Aula qualification, and Home Assistant action paths keep their legacy local assignments. |
| Other applications | Adopt `local-only` for portable text-only inference. Select a cloud role only after checking the data policy and enrolling a dedicated key. |

## Change and troubleshoot a route

1. Edit `agent_routing` in [capability-routes.json](../../config/capability-routes.json).
   Change a provider target once to affect its direct alias and every referencing
   role. Change one chain to affect only that role. The registry rejects
   repeating or indirect fallback targets and cloud deployments on local-only
   routes. If the mounted Spark runtime changes, verify the source deployment
   and served model name match the new runtime.
2. Run the registry renderer and checks from the root.

   ```sh
   python3 scripts/model_registry.py render
   python3 scripts/model_registry.py check
   git diff --check
   ./scripts/validate-repository.sh
   ```

3. Run the [runtime test](../../tests/litellm-agent-routing-runtime-test.py)
   against the pinned LiteLLM version. It exercises the real proxy and Router
   against synthetic loopback providers. For local development use an isolated
   Python environment with `litellm[proxy]==1.109.3` and `openai==2.33.0` to
   match the installed pin.
4. For a provider addition, first qualify its existing approved credential,
   model, protocol, supported efforts, context limit, client tools, and strict
   schema output. Record authentication and model parameters in the registry
   and extend the provider validation and renderer when a new authentication
   type is required. Keep secrets external. Add a paid provider only after
   approving the rate card, rate limits, and an enforced spend cap.

The observed gateway has one external failure domain. Sol, Luna, and Astra
share ChatGPT OAuth. DeepSeek, hosted Qwen, and Anthropic had no enrolled
gateway credential. The route ends when its selected cloud chain is exhausted.
The virtual-key caps limit request and token volume. LiteLLM reports zero
metered cost for subscription attempts and local inference. This tracks
gateway API spend independently of subscription fees or usage allowances.

Use the `x-litellm-model-id`, `x-litellm-model-name`, `x-litellm-model-group`,
and `x-litellm-attempted-*` headers to identify a selection. An ID such as
`agent-coder:sol:high` identifies the reviewed deployment and effort. If the
primary is already in cooldown, a successful request can use a different
deployment with zero fallback hops in that request. Review `HomeCompute route`
and `HomeCompute deployment failure` records in the container logs for latency
and sanitized failure types. The existing LiteLLM spend log records token
counts, model, and estimated costs. Correlate through the response call ID.
Use a scoped synthetic request over the consumer's actual network path when
checking service availability. An HTTP 200 from the gateway health endpoint
only confirms gateway liveness.

## Rollout and rollback

Capture the current Home Core release and applied gateway config, verify that
a compatible PostgreSQL backup and the previous release are available, and
check for a pending deployment override. Follow the
[Home Core lifecycle](../../docs/homecompute-operations.md) from a reviewed
revision on `origin/master`. Reconcile Home Core first. Refresh the Paseo catalog with its [dedicated lifecycle](../paseo/README.md#deploy-or-upgrade) when enrolled.
No Spark rollout is required for an alias-only change.

```sh
./scripts/homecompute deploy home-core --revision FULL_SHA
./scripts/homecompute status
./scripts/homecompute services
./scripts/homecompute doctor
./scripts/homecompute drift
```

Verify normal local inference, existing aliases, actual model response headers,
the enrolled and denied key paths, streaming, tool calls, schema output, and
the OpenCode tool loop. Use isolated fixture failures for downtime, rate limits,
and partial streams. Run real failure injection only during a separately
approved maintenance window. Test Paseo provider and model discovery after refreshing its catalog.

For rollback, return any enrolled consumer to its recorded model and credential
binding and revoke the new opt-in keys. Restore the OpenCode key's previous
model scope from the active release before redeploying the prior Home Core
release, so its prior provisioner accepts the key. This reuses the same token
and preserves its MCP scope. Use the Paseo runbook for its separate rollback procedure.

```sh
sudo python3 /srv/homecompute/releases/FULL_SHA/scripts/provision-opencode-litellm-key.py --apply --rollback
./scripts/homecompute deploy home-core --revision PRIOR_FULL_SHA
```

Confirm the legacy aliases and consumer paths after rollback. The LAN credential
is reconciled from the prior release's catalog. Agent sessions and workflow
data stay in their existing stores.
