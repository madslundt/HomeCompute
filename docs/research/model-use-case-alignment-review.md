# Model and use-case alignment review after the GB10 precision audit

Verified: 2026-08-30

Status: documentation reconciliation review. This note recommends benchmark
and canonical-document changes; it does not select a production model or
authorize a deployment change.

## Executive decision

The platform's model strategy remains sound: keep one efficient shared text
model first, load specialists serially, keep speech and retrieval as separate
services, and promote exact artifact/runtime tuples only from workload results.
The new GB10 precision evidence changes the **benchmark order and the wording of
several recommendations**, not the Phase C deployment.

Required conclusions:

1. Keep `nvidia/Qwen3.6-35B-A3B-NVFP4` as the first Phase C smoke-test
   artifact. NVIDIA publishes a dedicated one-DGX-Spark vLLM recipe for it, and
   its role breadth makes it the most useful first integration test. This is not
   a production selection.
2. Add `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` plus its official
   DSpark draft as the first efficient agent/coding performance challenger.
   It should move ahead of Devstral Small in the staged install order, but it
   does not replace Devstral's distinct controlled low-memory/latency-baseline
   role.
3. Keep `Qwen/Qwen3-Coder-Next-FP8` as the primary specialized coding quality
   candidate. No first-party Coder-Next NVFP4 artifact was found, and a local
   conversion would be a new artifact requiring its own accuracy qualification.
4. Treat `nvidia/Qwen3.6-27B-NVFP4` as a focused **precision and runtime
   control**, not another must-run quality tier. Gemma 4 31B NVFP4 already
   supplies a Spark-matrix-listed dense NVFP4 quality challenger, while
   Qwen3.8-27B FP8 supplies the newer dense Qwen quality challenger.
5. State explicitly that the first Qwen3.6 instance is intended to exercise
   `research` and `assistant` as well as `automation`, `home`, `meeting`, and
   `coding`. That is already true in Compose and the routing research. It does
   not mean all six roles are qualified by one 32K smoke test: Hermes
   `assistant` acceptance remains a separate 64K-or-greater test.
6. Do not change `config/compute-node.env.example` or
   `deploy/compute-node/compose.yaml` before benchmark evidence. Their single
   model, 32K context, two-sequence, MTP-off baseline is intentionally
   conservative and already warns that Hermes requires a later 64K test.

This review also corrects one source claim in
[`gb10-optimized-model-audit.md`](gb10-optimized-model-audit.md): the current
NVIDIA Spark vLLM support matrix does **not** contain a Qwen3.6-35B-A3B row.
The same NVIDIA playbook does publish a dedicated exact single-Spark recipe,
which is the evidence now cited by the audit.
[NVIDIA Spark vLLM matrix and dedicated Qwen3.6 recipe](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md#run-agent-ready-qwen36-35b-model-with-vllm)

## Quality fit and GB10 optimization are separate decisions

A model belongs in a role because it passes the role's quality, safety,
protocol, latency, and concurrency tests. Its precision format only determines
which exact artifact/runtime tuple should represent it in those tests.

`NVFP4` is therefore not a universal ranking signal. NVIDIA's Qwen3.6-35B
configuration describes the quantized MoE/MLP path as `W4A16_NVFP4`, and the
Spark launch recipe selects Marlin. NVIDIA is even more explicit for Nemotron
3.5 Lightning: on GB10 the artifact is stored in NVFP4 but computes through a
W4A16/Marlin path, with the native FP4 Tensor Core path marked "No." These
tuples can still save substantial model memory and memory traffic, but neither
proves native W4A4 execution. Capture the selected kernels, memory, and end-to-
end performance instead of inferring acceleration from the filename.
[Qwen3.6-35B quantization configuration](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4/blob/main/hf_quant_config.json),
[Nemotron 3.5 hardware matrix](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4#model-summary),
[vLLM ModelOpt kernel selection](https://docs.vllm.ai/en/latest/features/quantization/modelopt/)

## Use-case disposition

| Use case | Quality and integration need | Recommended benchmark disposition | Precision consequence on GB10 |
| --- | --- | --- | --- |
| Home Assistant text/voice reasoning | Danish, exact tool/entity arguments, zero unsafe execution, short spoken replies, and p95 latency under mixed load | Keep Qwen3.6-35B-A3B first. If it misses latency, compare the newer small quality candidate `Qwen/Qwen3.5-4B` with NVIDIA's Spark-listed `nvidia/Qwen3-8B-NVFP4` and `nvidia/Qwen3-14B-NVFP4`. The former is a newer small-model quality hypothesis; the latter two are stronger first-party Spark artifact hypotheses. Do not assume any wins Danish tool use. | Small NVFP4 artifacts are conditional reserved-process controls, not automatic replacements. Nemotron 3.5 should not be a presumptive `home` candidate because its published post-training language list omits Danish. [NVIDIA Spark model matrix](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md#model-support-matrix), [Nemotron languages](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4#model-summary) |
| Coding through Codex | Repository-scale implementation, valid Responses/tool loops, build/test success, correction after failures, and acceptable interactive latency | Keep Coder-Next FP8 first for specialized coding quality. Add Nemotron 3.5 Lightning target-only, native MTP, and DSpark as separate agent-performance tuples. Keep Gemma 4 31B NVFP4 and Qwen3.8-27B FP8 as dense controls; keep Devstral Small as a lower-resource operational control, not ahead of the NVIDIA Spark-specific challenger. | Do not substitute a community or locally converted Coder-Next NVFP4 artifact before the publisher FP8 baseline. Qwen says the FP8 checkpoint is 80B/3B-active, 262K, non-thinking, and intended for long-horizon coding agents, but also says the card's displayed evaluations used the BF16 model. Local FP8 quality is therefore part of the gate. [Qwen Coder-Next FP8 card](https://huggingface.co/Qwen/Qwen3-Coder-Next-FP8#model-overview) |
| Automation and private research | Danish/English structured output, source-bounded synthesis, strict schemas, prompt-injection resistance, ordinary read-only tools, and no model-owned durable state | Map both `automation` and `research` to Qwen3.6 initially. Test Nemotron 3.5 on English agent/research fixtures and coding, but do not promote it to Danish Aula, household briefing, or private Danish Notion work without local evidence. Keep gpt-oss-120b and Nemotron Super as serialized difficult-task controls. | Use publisher-native MXFP4 first for gpt-oss; an NVFP4 cast is a separate experimental artifact. Nemotron 3.5's DSpark draft is a latency mechanism, not another role model. [OpenAI gpt-oss card](https://huggingface.co/openai/gpt-oss-120b), [NVIDIA DSpark card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4-DSpark) |
| Meeting summarization | Faithful cleanup and versioned summary/decision/action schemas over immutable transcript layers; local-only content | Keep `meeting` on the shared Qwen3.6 baseline first and evaluate it with meeting-specific factuality and schema fixtures. Dense and high-capacity text challengers are useful only if they improve those outputs enough to justify load/swap cost. | Text precision does not select the transcription model. STT and diarization remain separate services and capacity consumers. |
| Personal agents / Hermes | Tool reliability, profile isolation below the model, 64K minimum active-session context, mixed-load headroom, and the NemoClaw/Hermes OpenAI-compatible path | Continue with Qwen3.6 because it is the documented NemoClaw managed-vLLM Spark default and already exercises the shared gateway. Expose it through `assistant`, but qualify the role at 64K or greater. Nemotron 3.5 may be a later English agent-performance challenger, not a reason to change the initial Hermes path. | The Phase C 32K server is a protocol/smoke baseline, not an accepted Hermes tuple. Keep the config unchanged and create a separately pinned 64K benchmark profile later. [NVIDIA NemoClaw inference-provider guide](https://docs.nvidia.com/nemoclaw/user-guide/hermes/inference/learn-and-choose/choose-inference-provider) |
| STT | Low-latency Danish household speech plus robust Danish/English and code-switched meetings | Keep Danish Parakeet, Whisper large-v3-turbo, and Whisper large-v3 in the existing audio scorecard. A fast home winner and a slower meeting winner may differ. | NVIDIA Model Optimizer currently lists Whisper for FP8, not NVFP4. Do not add an FP4 conversion track until the unmodified candidates reveal an actual memory or throughput problem. [NVIDIA Model Optimizer support matrix](https://github.com/NVIDIA/Model-Optimizer/blob/main/examples/hf_ptq/README.md#hugging-face-supported-models) |
| TTS | Danish pronunciation, naturalness, first-audio latency, Wyoming/Home Assistant reliability, and an outage fallback | Keep Piper `da_DK-talesyntese-medium` as the operational baseline and Røst 350M/500M as controlled naturalness challengers under their existing license/runtime gates. | LLM NVFP4 findings do not apply. CPU Piper may be preferable because it preserves GB10 capacity and remains available independently of the main GPU model. |
| Embeddings and reranking | Owner-scoped private retrieval with measured recall/ranking quality and no cross-profile leakage | Keep `Qwen/Qwen3-Embedding-0.6B` and `Qwen/Qwen3-Reranker-0.6B` deferred until a concrete private-RAG corpus exists. Benchmark 0.6B first, then 4B only for a material retrieval gain. | NVIDIA's Spark matrix lists different Qwen3-VL embedding/reranker identities, not these 0.6B text models. Their small size and retrieval quality matter more than manufacturing an NVFP4 variant. [Qwen embedding card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B), [Qwen reranker card](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B), [NVIDIA Spark model matrix](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md#model-support-matrix) |

## Required canonical-document updates

These changes should be made before the Phase D benchmark manifest is frozen.
They do not require changing accepted ADR decisions.

| Document | Required update | Reason |
| --- | --- | --- |
| [`llm-installation-recommendation.md`](llm-installation-recommendation.md) | Add `research` and `assistant` to Qwen3.6's exercised roles, with the explicit 64K Hermes caveat. Insert Nemotron 3.5 Lightning plus DSpark ahead of Devstral Small as the first efficient agent/coding performance challenger. Keep Devstral as a controlled lower-resource baseline rather than deleting it. Put Qwen3.6-27B in a focused precision-control track rather than the mandatory quality ladder. Say gpt-oss is benchmarked in native MXFP4 first. Add a short separate cross-reference for speech and deferred retrieval so “model installation order” cannot be mistaken for an all-modality list. | This is the canonical staged shortlist, and it no longer reflects the newer NVIDIA artifacts or all six aliases already exposed by Compose. |
| [`general-model-evaluation.md`](general-model-evaluation.md) | Expand its stated scope from `automation`/`meeting` to `automation`/`research`/`meeting`. Add Nemotron 3.5 as an English agent/performance candidate with Danish and license caveats. Add Qwen3.6-27B only as an optional dense NVFP4 precision control beside Qwen3.8 FP8, not as another presumed quality step. | The fixtures already include research synthesis, while the recommendation text omits the `research` alias. |
| [`coding-model-evaluation.md`](coding-model-evaluation.md) | Add Nemotron 3.5 Lightning target-only/MTP/DSpark tuples immediately after Coder-Next, including OpenMDW-1.1 review and Codex protocol tests. Clarify that Devstral Small remains a resource/latency control. Do not replace Coder-Next FP8 with an unqualified NVFP4 derivative, and record that Qwen's published card evaluations were run on BF16 rather than the FP8 artifact. | Nemotron now has the strongest first-party single-Spark low-concurrency agent-performance path among the omitted coding challengers, while Coder-Next FP8 still lacks publisher quality results for that exact precision tuple. |
| [`home-assistant-model-evaluation.md`](home-assistant-model-evaluation.md) | Reconcile the small-model shortlist: retain Qwen3.5-4B as the newer small quality candidate, add NVIDIA Qwen3-8B/14B NVFP4 as Spark-listed optimized controls, and test them only if shared Qwen3.6 misses mixed-load latency. | The current page predates both the small-model audit and the GB10 artifact audit. Precision support and model quality are competing hypotheses, not one replacement decision. |
| [`implementation-plan.md`](../implementation-plan.md) | Add Nemotron 3.5 Lightning/DSpark to D2's named comparisons; mark Qwen3.6-27B as optional precision A/B. In D1, either add explicit `research` and `assistant` fixture groups or state exactly which general/Hermes suites qualify those aliases; require the `assistant` run at 64K or greater. | Gate D says every alias must have a qualified tuple, but the fixture list does not explicitly account for `research` or `assistant`. |
| [`requirements.md`](../requirements.md) | Bind `research`, `meeting`, and `assistant` to explicit quality/scorecard requirements, or explicitly state which existing general, meeting, and personal-assistant requirements qualify each alias. Add retrieval quality and cross-scope leakage gates when the deferred embedding/reranker service enters scope. | Alias existence alone is not a testable model-selection criterion. Precision names should remain out of requirements. |
| [`docs/README.md`](../README.md) | Add the GB10 precision audit to the “Current model shortlist” entry points and update the challenger summary to mention Nemotron 3.5. Preserve Qwen3.6 as the first candidate. | The documentation guide says newer evidence should be reconciled into the canonical shortlist, but its summary currently stops at Gemma/Coder-Next/Qwen3.8. |
| [`gb10-optimized-model-audit.md`](gb10-optimized-model-audit.md) | Corrected in this review: Qwen3.6-35B has a dedicated single-Spark recipe but no row in the generic support matrix. A later canonical reconciliation can also state directly that the published Qwen3.6 quantization configuration is W4A16 NVFP4 for the MoE/MLP path. | Primary-source accuracy; this is not a recommendation change. |

## No required ADR or deployment change

- [`README.md`](../../README.md) can continue to name Qwen3.6 NVFP4 as the
  first deployment candidate. It already says the model is not selected for
  production.
- ADR-002 (vLLM first), ADR-004 (semantic aliases), ADR-008 (separate `home`
  qualification), ADR-012 (Meeting Assistant ownership), and ADR-013 (Hermes
  outside GB10 with a 64K re-test) remain consistent with the evidence. Do not
  revise an ADR until measured results select a different runtime, alias
  mapping, or placement.
- The current Compose and environment template should remain the Qwen3.6,
  single-model, 32K, MTP-off Phase C baseline. Nemotron, Coder-Next, dense
  challengers, 64K Hermes, and alternate speculative-decoding settings each
  need separate model-specific release manifests or benchmark profiles; they
  must not inherit Qwen3.6's parser/backend flags by changing only `MODEL_ID`.

## Optional updates to dated research

The older omission, attached-recommendation, and MTP notes are historical
evidence and need not be rewritten if the canonical shortlist and this review
are linked clearly. The current
[`n8n-model-routing-and-scheduling.md`](n8n-model-routing-and-scheduling.md)
already names Nemotron 3.5, Qwen3.5-4B, and the 0.6B retrieval models. An
optional clarification could add Qwen3-8B/14B NVFP4 as optimized small controls
and label Qwen3.6-27B as a precision-only experiment; neither changes its
one-shared-model starting policy.

## Promotion rule

No alias mapping should change until the exact revision, precision layout,
runtime/container, parser/template, context, speculative-decoding mode, and
kernel path pass that alias's workload fixtures and the required mixed-load
memory gate. “GB10 optimized” is evidence for which tuple to test, not evidence
that the model is the best fit for a household, coding, research, meeting, or
personal-agent task.
