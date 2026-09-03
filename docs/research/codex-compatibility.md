# Codex compatibility research

Verified: 2026-08-25

Status: conditional go for a pinned Codex 0.145.0 cross-provider proof of
concept; current 0.149.1 behavior is incompatible with the target role override

## Decision

Keep Codex as the developer-facing harness and configure a machine-local custom
provider for the GB10. The installed Codex CLI is 0.145.0. Its role-layer source
explicitly allows a custom agent config file to override `model_provider`, so it
is a valid candidate for the cloud-parent to GB10-child PoC.

This capability is version-fragile. Codex 0.149.1 changed role application to a
bounded override structure that includes the model but omits `model_provider`.
Independent reports on 0.146.0 also show a correctly selected custom-provider
child losing the delegated task payload. Therefore Stage 2 is not accepted
until the installed 0.145.0 completes the exact cross-provider handoff and GB10
Responses/tool suite. Upgrading the client requires requalification and may
disable the workflow.

## Verified capabilities

- Codex supports custom model providers with a `base_url`, authentication
  settings, headers, and retry/stream timeout settings. The only documented
  custom-provider wire protocol is `responses`.
- Provider definitions and the active `model_provider` are machine-local
  settings. Project `.codex/config.toml` files cannot override them. Installation
  therefore needs an explicit user-level configuration step rather than a
  repository-only configuration.
- Codex has built-in `ollama` and `lmstudio` provider identifiers and an `--oss`
  path, but the GB10 API is better represented as a named custom provider so the
  stable network endpoint and authentication remain explicit.
- The installed `codex-cli 0.145.0` was verified locally on 2026-08-25. Its
  tagged source loads a custom agent file as a high-precedence configuration
  layer and preserves the parent's provider only when the layer does not set
  `model_provider`.
- Codex CLI 0.149.1 uses a typed `AgentRoleOverrides` allow-list containing
  `model`, reasoning, personality, service tier, capability reductions and
  skills, but not `model_provider`. A 0.149.1 custom agent therefore cannot use
  this mechanism to select GB10 from a cloud-parent session.
- Cross-provider child selection alone is insufficient. Open Codex issues on
  0.146.0 document custom-provider children receiving an empty/unusable dynamic
  assignment, including encrypted-content behavior. The 0.145.0 PoC must prove
  task and follow-up payload delivery, not merely inspect child provider metadata.
- The Responses API supports streaming and function-call items. Compatibility
  means matching its event and item semantics, not merely exposing an endpoint
  named `/v1/responses`.
- vLLM now publishes a dedicated Codex integration guide. It states that vLLM
  implements the Responses API used by Codex, documents the user-level custom
  provider configuration, and requires a model with the correct tool-call
  parser. This makes vLLM the strongest PoC runtime candidate, while still not
  replacing an end-to-end test with the exact model and GB10 image.

Sources: [Codex configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference),
[Codex subagents and custom agents](https://learn.chatgpt.com/docs/agent-configuration/subagents),
[OpenAI Responses API reference](https://developers.openai.com/api/reference/typescript/resources/beta/subresources/responses/methods/create),
[vLLM Codex integration](https://docs.vllm.ai/en/latest/serving/integrations/codex/),
[Codex 0.145.0 subagent role source](https://github.com/openai/codex/blob/rust-v0.145.0/codex-rs/core/src/agent/role.rs),
[Codex 0.149.1 bounded role source](https://github.com/openai/codex/blob/rust-v0.149.1/codex-rs/core/src/agent/role.rs),
[custom-provider task-payload issue on 0.146.0](https://github.com/openai/codex/issues/35932),
[cross-provider encrypted assignment issue](https://github.com/openai/codex/issues/34833).

## Proposed Stage 2 configuration boundary

Machine-local, installed with user confirmation:

```toml
[model_providers.gb10]
name = "GB10"
base_url = "https://ai.home.arpa/v1"
env_key = "GB10_AI_API_KEY"
wire_api = "responses"
requires_openai_auth = false
```

Candidate custom-agent intent for the pinned 0.145.0 PoC:

```text
planner       -> cloud provider / frontier model
implementer   -> gb10 provider / coding
reviewer      -> cloud provider / frontier model
```

The `gb10` provider definition remains user-level. A 0.145.0 custom implementer
agent config may set the documented top-level `model_provider = "gb10"` and
`model = "coding"`; the exact file is deferred until the endpoint exists so
it can be exercised immediately. No such file is activated on 0.149.1 because
that version drops the provider field during bounded role projection.

## Automatic routing and fallback

Codex documents agent orchestration and per-agent model configuration, but it
does not document a declarative plan/implement/review state machine or an
automatic "retry twice, then change provider" policy. If the pinned 0.145.0
cross-provider handoff canary passes, the smallest credible solution is a
repository skill or orchestration instruction that:

1. delegates an approved bounded task to the local implementer;
2. runs the specified verification command;
3. returns compiler/test output to one local retry;
4. delegates to a cloud implementation agent after the retry budget or an
   explicit escalation condition; and
5. delegates the final diff to a cloud reviewer.

Until then, use two explicit whole-session modes—cloud Codex or GB10 Codex—and
do not claim the Stage 2 automatic workflow. After the 0.145.0 canary passes,
the orchestration preserves the single `codex` UX without claiming transparent
provider failover inside one model request. Every future Codex upgrade reruns
the canary before activation.

## PoC acceptance tests

| Test | Pass condition |
| --- | --- |
| Provider startup | Codex starts with the user-level `gb10` provider and no undocumented config |
| Responses request | GB10 accepts the exact request emitted by Codex |
| SSE streaming | Text, reasoning, and tool events are parsed without retries or dropped items |
| Tool calling | Shell/read/write/apply-patch calls round-trip with valid call IDs and JSON arguments |
| Long task | Context compaction or continuation completes a representative repository task |
| MCP | Parent and local implementer retain the expected MCP/tool availability |
| Version pin | The executed client is the qualified 0.145.0 binary; an unqualified upgrade is rejected |
| Agent routing | Child session metadata proves cloud planner/reviewer and GB10 `coding` implementer |
| Task delivery | Unique assignment and follow-up canaries are visible to the GB10 child and executed correctly |
| GB10 outage | Planning/review still work; implementation escalates with an explicit recorded reason |
| Retry budget | One local retry receives failure evidence; the next failure selects cloud implementation |
| Metrics | provider, logical model, attempt, result, and fallback reason are recorded without prompts |

## Open questions for the PoC

- Whether the chosen inference server implements enough Responses API semantics
  for the installed Codex version, including reasoning and tool item ordering.
- Whether installed 0.145.0 delivers the full initial and follow-up task payload
  to the custom-provider child, without provider mis-selection or encrypted-only
  content.
- Whether the Codex desktop/app path uses the same qualified 0.145.0 behavior as
  the CLI, and how the client can be pinned safely until a newer release passes.
- Whether Codex uses a compact endpoint or client-side compaction for this model
  and whether the local provider handles the observed behavior.
- Whether a local coding model reliably produces the exact tool-call format
  expected by Codex under multi-turn failure recovery.
