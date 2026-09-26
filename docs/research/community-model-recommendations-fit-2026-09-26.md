# Community model recommendations: identity and HomeCompute fit

Verified: 2026-09-26

Scope: verify the three models named in a community reply and assess them for
the current 128 GiB NVIDIA GB10 stack. This is a source and compatibility
review, not a deployment or a claim that publisher benchmarks reproduce on
`home-spark`.

## Bottom line

The three repositories are real, but the description "also fine tunes" is
misleading:

- **Qwen3.6 Instruct Revised is not a fine-tune.** It is the official
  Qwen3.6-35B-A3B converted to GGUF with a community chat template. Its author
  explicitly says model capability is unchanged. It offers no reason to
  replace the already-running Qwen3.6 NVFP4 automation model; the template is
  the only feature worth A/B testing.
- **SC117 Ornith Heretic MTP APEX is an experimental compound derivative.** It
  combines the Ornith agentic model, a refusal-modifying Heretic LoRA, a grafted
  MTP head, and APEX GGUF quantization. Upstream Ornith has interesting coding
  and tool benchmark results, but those results do not validate the SC117
  derivative. It is unsuitable as a trusted Home Assistant or n8n default.
- **Gemma 4 12B QAT is the useful candidate.** It is an official
  Google QAT target plus a separate speculative assistant, packaged by Unsloth
  as GGUF. It is the best of these three to A/B against the current Gemma 4 E4B
  `home-fast` service. For this stack, use Google's compressed-tensors target
  without MTP through the current vLLM rather than changing the appliance to
  the Unsloth llama.cpp package. Qualify vLLM 0.29 separately before enabling
  the official assistant; Gemma 4 12B MTP had a CUDA-graph failure in the
  v0.26-v0.27 generation used on `home-spark`.

Do not add another always-resident LLM. On inspection, `home-spark` had 46 GiB
available while already running Qwen3.6, Gemma E4B, Hviske, and Plapre, with
9.3 GiB of swap in use. Test Gemma 12B **in place of** E4B, and test an Ornith
or Occamy candidate **in place of** the current automation model.

## Exact identities

| Quoted name | Verified artifact | What it actually is | License/provenance |
| --- | --- | --- | --- |
| Qwen 3.6 35B A3B Instruct Revised | [`Smoffyy/Qwen3.6-35B-A3B-Instruct-Revised-GGUF@eb7ecc7`](https://huggingface.co/Smoffyy/Qwen3.6-35B-A3B-Instruct-Revised-GGUF/tree/eb7ecc79a0074a50655558fe65c75005d7729551) | GGUF conversion of official post-trained Qwen3.6 with a custom Jinja chat template. The card says it is otherwise capability-identical to the pure conversion. [Pinned card](https://huggingface.co/Smoffyy/Qwen3.6-35B-A3B-Instruct-Revised-GGUF/blob/eb7ecc79a0074a50655558fe65c75005d7729551/README.md) | Apache-2.0; repository includes a license file. |
| Ornith 1.5 35B Heretic Apex MTP | [`SC117/Ornith-1.5-35B-A3B-Heretic-MTP-APEX-GGUF@d5e6a53`](https://huggingface.co/SC117/Ornith-1.5-35B-A3B-Heretic-MTP-APEX-GGUF/tree/d5e6a5319d59e0f457b50fa86d9c959a1109cec3) | Heretic 1.4 Trial 62 LoRA merged into Ornith 1.5, native MTP tensors replaced with a separately distilled Qwen3.6-derived head, then APEX-quantized to GGUF. [Pinned card](https://huggingface.co/SC117/Ornith-1.5-35B-A3B-Heretic-MTP-APEX-GGUF/blob/d5e6a5319d59e0f457b50fa86d9c959a1109cec3/README.md) | Card metadata says MIT and links to upstream Ornith's MIT license, but the SC117 manifest has no license file; the grafted MTP source is Apache-2.0. Preserve this provenance caveat. |
| Gemma 4 12B QAT MTP from Unsloth | [`unsloth/gemma-4-12B-it-qat-GGUF@980b060`](https://huggingface.co/unsloth/gemma-4-12B-it-qat-GGUF/tree/980b060c40a8539ac159e0501a3e0f66a6365af3) | Unsloth Dynamic GGUF of Google's QAT target plus separate Gemma 4 assistant/drafter files. MTP is speculative decoding, not a fine-tune of the target. [Pinned MTP documentation](https://huggingface.co/unsloth/gemma-4-12B-it-qat-GGUF/blob/980b060c40a8539ac159e0501a3e0f66a6365af3/MTP/README.md) | Apache-2.0; model metadata links Google's Gemma 4 license terms. |

The Smoffyy card sometimes says "36B" in prose. The official repository and
model metadata say **35B**, so 36B should be treated as a typo.

## Technical comparison

| Candidate | Architecture and context | Artifact footprint | Tools and languages | Current-stack compatibility |
| --- | --- | --- | --- | --- |
| Qwen3.6 Revised | Multimodal MoE, 35B total / 3B active, 40 layers, 256 experts, 8 routed plus 1 shared; 262,144 native context. The official base has one native MTP layer. [Official pinned config](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/blob/995ad96eacd98c81ed38be0c5b274b04031597b0/config.json) | Q3 16.76 GB; Q4_K_M 21.17 GB; Q5_K_M 24.73 GB; Q8 36.90 GB; optional vision projector 0.90 GB. The publisher reports about 21 GB at 32K for Q4_K_M. | Official Qwen supports OpenAI-style tool serving with `qwen3_coder`. The exact community artifact publishes no Danish result. "Revised" changes thinking/developer-role/template behavior, not weights. [Official Qwen card](https://huggingface.co/Qwen/Qwen3.6-35B-A3B/blob/995ad96eacd98c81ed38be0c5b274b04031597b0/README.md) | Not a drop-in replacement: the production launcher requires fastsafetensors and the appliance has no llama.cpp server or vLLM GGUF plugin. Even if a GGUF lane is added, it duplicates the current Qwen3.6 capability. |
| Ornith Heretic MTP APEX | Qwen3.5-family multimodal MoE, 35B / 3B active, 40 trunk layers plus a fused MTP layer, 256 experts / 8 active; 262,144 context. | APEX I-Quality 23.49 GB, I-Balanced 26.06 GB, I-Compact 17.33 GB, I-Mini 14.27 GB; optional vision projector 0.90 GB. | Upstream Ornith documents `qwen3_xml`/`qwen3_coder` tool parsing and reports MCP-Atlas 70.2 and Toolathlon-Verified 48.7, but SC117 publishes no independent post-Heretic/post-quant tool evaluation. Neither source publishes Danish results. [Upstream pinned card](https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B/blob/10fbf86fed7ecee4a061f8b499a618f46001cac1/README.md) | SC117 documents llama.cpp with `--draft-mtp`; it does not document vLLM. The current host has neither llama.cpp nor a GGUF plugin. It therefore needs a separate experimental runtime and should replace, not accompany, the automation model during testing. |
| Gemma 4 12B QAT MTP | Dense, unified/encoder-free Gemma 4, 11.95B parameters, 48 layers, 256K context; text/image/audio input and video via frames, text output. [Google model card](https://huggingface.co/google/gemma-4-12B-it) | Unsloth target 6.716 GB plus recommended Q4_0 drafter 0.254 GB and optional multimodal projection 0.175 GB. The more natural vLLM target, [`google/gemma-4-12B-it-qat-w4a16-ct@1d2c2d7`](https://huggingface.co/google/gemma-4-12B-it-qat-w4a16-ct/tree/1d2c2d7f2466070e69d6fb3fd5ce9a7d75f2f6ee), has 10.264 GB of safetensors; its official assistant is 0.846 GB. | Google documents native function calling, 35+ out-of-box languages, and pretraining across 140+ languages, but does not publish a Danish result. [Google card](https://huggingface.co/google/gemma-4-12B-it#core-capabilities) | The installed vLLM `0.27.1+93523f72.nv26.8.64249418` can serve the W4A16 target and exposes the `gemma4` tool parser. Do the first test without MTP. Community reproduction shows 12B MTP tripped CUDA graph capture on v0.26-v0.27.x and was fixed in v0.29.0; qualify that runtime before adding the official assistant. [GB10/vLLM reproduction](https://github.com/kelnei/vllm-gemma4#enabling-speculative-decoding) |

vLLM's current documentation treats Gemma 4 assistants as a special MTP path
that shares KV cache with the target. It supports the 12B assistant, recommends
starting with one speculative token, and warns that older versions may
incorrectly load it as a generic draft model. [vLLM MTP
documentation](https://github.com/vllm-project/vllm/blob/main/docs/features/speculative_decoding/mtp.md)
The vLLM project also describes GGUF support as experimental; its separate GGUF
plugin demonstrates embedded Qwen3.5 MTP loading, but that plugin is not
installed on `home-spark`. [vLLM GGUF
plugin](https://github.com/vllm-project/vllm-gguf-plugin)

## Fit by role

### Home Assistant: test Gemma 12B, keep E4B as the default until it wins

Gemma 12B is the only one of the three that is a sensible challenger for the
fast Home Assistant fallback. It brings more dense capacity than E4B, native
tool syntax, and a supported vLLM MTP path. It is also likely slower per decoded
token than E4B; MTP may recover some of that latency, but Unsloth's published
0.51 draft acceptance was measured on a B200, not a GB10. It is not a GB10
result and does not prove a faster voice round trip.

Run it as text-only with thinking disabled, a 32K cap, and no speculative
assistant initially. Compare it against the existing E4B service with identical
Danish prompts. Promote it only if it improves intent/tool correctness without
losing the current interactive latency. Built-in deterministic Home Assistant
intents should remain ahead of either model. Test MTP only after separately
qualifying vLLM 0.29 or newer; do not change the production image merely to run
this A/B test.

### n8n: keep the current Qwen3.6 baseline; Ornith upstream is a challenger

The Revised Qwen artifact contains the same Qwen3.6 weights already serving the
`automation-moe` lane. Switching to it would change runtime and quantization
merely to obtain a different template. If developer-role or thinking-history
behavior is a real issue, extract and A/B the template in the current vLLM
profile first.

Upstream Ornith 1.5 is worth a controlled n8n benchmark because its publisher
reports better tool and agentic coding scores than Qwen3.6. The SC117 Heretic
artifact is not the right first artifact: refusal-direction editing is actively
undesirable for a model that can operate workflows or physical devices, and
neither its grafted MTP head nor its mixed quant has the upstream benchmark
evidence. Test a pinned upstream-derived, vLLM-compatible quant first. Only test
the SC117 package later as an offline performance/behavior experiment.

### Coding and general assistant use

Ornith upstream has the strongest quoted case for agentic coding; its card
reports Terminal-Bench 2.1 67.8, SWE-bench Verified 79.0, and SWE-bench Pro
59.6. Those are publisher measurements with specific harnesses, long contexts,
and sampling, and do not transfer automatically to the Heretic APEX artifact.
Gemma 12B is a useful compact general model but is not the likely replacement
for Qwen3.8-27B on difficult coding work. Qwen Revised is simply the existing
Qwen3.6 capability in another package.

## Occamy context from the linked reply

The linked [`Accio-Lab/occamy-1.0@f9e2771`](https://huggingface.co/Accio-Lab/occamy-1.0/tree/f9e2771699f14d9c4bbbc0c7b913e58ebe1442d0)
is a fourth, separate candidate. It retains Qwen3.6's 35B/3B-active architecture
but is post-trained for long-horizon co-work, tool use, structured APIs, state
tracking, and recovery. Its publisher reports a larger AutomationBench gain
over Qwen3.6 than the other candidates and supplies a 23.926 GB
[`NVFP4 checkpoint@2e53d70`](https://huggingface.co/Accio-Lab/occamy-1.0-NVFP4/tree/2e53d70641794e441cb038d2499a9e4b8cad306a)
that is closer to the current vLLM deployment format. Its separate MTP head is
explicitly experimental, and there is still no Danish evidence. For n8n,
Occamy NVFP4 is a more relevant second challenger than SC117 Heretic, after the
current Qwen3.6 baseline is fully measured. [Occamy pinned model
card](https://huggingface.co/Accio-Lab/occamy-1.0/blob/f9e2771699f14d9c4bbbc0c7b913e58ebe1442d0/README.md)

## Required benchmark gates

Use fixed, redacted fixtures and compare exact model/revision/runtime tuples.

1. **Danish:** short colloquial commands, Danish letters and names, areas and
   entity aliases, dates/numbers, negation, corrections, English/Danish code
   switching, and native-speaker review. None of these artifacts has a
   published Danish acceptance result.
2. **Home Assistant:** correct tool and entity, correct no-call behavior,
   confirmation before consequential actions, no invented entities, p50/p95
   time to first token and full response, and end-to-end Hviske-to-Plapre
   latency under mixed load.
3. **n8n:** exact JSON Schema arguments, no-call decisions, sequential and
   parallel tool calls, tool-result continuation, errors/retries/idempotency,
   prompt injection inside tool data, and at least 64 exposed tools.
4. **MTP:** baseline off before on; record draft acceptance by position,
   output tokens/s, end-to-end task latency, and output/tool equivalence. MTP
   should be rejected if it is merely a faster token stream that increases
   retries or malformed calls.
5. **GB10 operations:** cold load and restart time, peak unified memory,
   32K/64K context behavior, concurrent speech load, 24-hour soak, OOM recovery,
   and at least 20 GiB of practical recovery headroom. Never infer coexistence
   from checkpoint file sizes alone.
6. **Safety:** separately red-team any Heretic/refusal-modified model. It must
   not receive Home Assistant or production workflow authority merely because
   it scores better on a coding benchmark.

## Recommended order

1. Keep Qwen3.6 NVFP4 serving n8n and retain its current Danish/tool baseline.
2. A/B the Qwen Revised template only if an observed template defect warrants
   it; do not deploy its GGUF as a new model.
3. Replace E4B temporarily with official Gemma 4 12B QAT W4A16 without MTP.
   Keep E4B if 12B misses the voice latency gate. Only then qualify vLLM 0.29+
   in a separate profile and test the official assistant.
4. Benchmark Occamy NVFP4, then an upstream Ornith-derived vLLM-compatible
   artifact, as exclusive n8n/coding challengers.
5. Keep SC117 Heretic APEX MTP out of trusted routes unless it passes the full
   provenance, safety, Danish, tool, and runtime gates in an isolated lane.
