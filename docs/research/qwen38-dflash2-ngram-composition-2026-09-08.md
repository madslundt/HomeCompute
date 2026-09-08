# Qwen3.8-27B: DFlash2 plus prompt-lookup speculation on GB10

Verified: 2026-09-08

Status: upstream research; no runtime configuration changed

## Bottom line

**Neither vLLM nor SGLang can compose DFlash2 (or native MTP) with prompt
lookup/N-gram speculation in one decode request today.** Both runtimes select
one speculative proposer. In vLLM, `SpeculativeConfig.method` is a single value
such as `mtp`, `dflash`, or `ngram`; its validator initializes N-gram lookup
only for an N-gram method and zeroes the prompt-lookup fields for other methods.
[vLLM speculative configuration](https://github.com/vllm-project/vllm/blob/e0aaef85f3de7a2376aee3708a3cba96286b41b1/vllm/config/speculative.py#L1091-L1203)
SGLang similarly exposes one `--speculative-algorithm`, with `DFLASH`, native
MTP/EAGLE-family methods, and `NGRAM` as separate workers. Its roadmap still
lists a hybrid EAGLE-plus-N-gram algorithm as future work; there is no DFlash
hybrid implementation, flag, example, or benchmark.
[SGLang algorithm selection](https://github.com/sgl-project/sglang/blob/afe90a8bc908002219993794404c7c95dd3ced1d/python/sglang/srt/speculative/spec_info.py#L33-L47),
[SGLang hybrid roadmap](https://github.com/sgl-project/sglang/issues/12780)

Therefore the requested third benchmark arm, “DFlash2 + prompt/N-gram lookup,”
is **unsupported**, not merely untuned. Do not invent a combined JSON object or
place N-gram flags beside `DFLASH`; at best they are ignored or rejected, and
they do not create a second proposer.

The GB10 can run the individual methods. Native MTP is already the repository's
baseline. SGLang 0.5.19 now ships DFlash2 and quantized-target-`lm_head`
support. A real GB10 report in the SGLang tracker demonstrates Qwen3.8-27B
NVFP4 plus DFlash2 on `sm_121`; that run predates 0.5.19 and used a pinned image
plus merged source overlays, so it proves hardware feasibility rather than the
tagged release's exact tuple.
[SGLang 0.5.19 release](https://github.com/sgl-project/sglang/releases/tag/v0.5.19),
[SGLang GB10 verification issue](https://github.com/sgl-project/sglang/issues/35860#issue-3362640469)
vLLM 0.28.0 is the first tagged vLLM release containing DFlash2 and publishes
CUDA 12.9/13.0 ARM64 wheels, but recent Qwen3.8/GB10 correctness and cache
regressions make DFlash2 an isolated experiment rather than a mature default.
[vLLM 0.28.0 release](https://github.com/vllm-project/vllm/releases/tag/v0.28.0)

## What “high context reuse” actually means

Three different optimizations should not be conflated:

1. **Prefix caching** avoids prefill work when later requests repeat the same
   prompt prefix. This improves TTFT, not the number of output tokens accepted
   per verifier pass.
2. **vLLM prompt lookup/N-gram speculation** searches the current request's
   prompt-and-output token history for a suffix match and proposes the following
   tokens. It helps when the *generated output copies or repeats token spans*
   already present in that history—for example, applying a mostly mechanical
   refactor to code included in the prompt. Merely sending the same large
   repository context on successive requests is a prefix-cache opportunity,
   not automatically an N-gram opportunity.
   [vLLM N-gram proposer](https://github.com/vllm-project/vllm/blob/e0aaef85f3de7a2376aee3708a3cba96286b41b1/vllm/v1/spec_decode/ngram_proposer.py#L207-L291)
3. **SGLang NGRAM** currently builds its cache from previously generated
   tokens; automatic SAM lookup over the user's current input remains a roadmap
   item. SGLang can preload an external JSONL corpus, which could contain
   representative code, but that is a separate NGRAM-only service and consumes
   additional CPU memory.
   [SGLang NGRAM roadmap](https://github.com/sgl-project/sglang/issues/21052#L199-L203),
   [external-corpus implementation](https://github.com/sgl-project/sglang/pull/21425)

For this coding-agent workload, repeated repository prefixes may matter more
than N-gram proposal rate. That makes current prefix-cache regressions especially
important: on one GB10, vLLM nightly with this exact
`unsloth/Qwen3.8-27B-NVFP4` family reported zero prefix-cache hits with either
MTP or DFlash, while no-speculation reused 17,248 of an 18,219-token repeated
prompt and reduced second-request TTFT from 9.46 s to 0.64 s. The issue also
states that v0.24.0 MTP did reuse prefixes, so this is a regression to pin and
test, not a fundamental limitation.
[vLLM GB10 prefix-cache regression](https://github.com/vllm-project/vllm/issues/54360#issue-3389129979)

## Expected benefit and its limits

The best upstream DFlash2 numbers are promising but are not GB10 numbers. The
publisher measured Qwen3.8-27B on one H200, block size eight, using the model's
recommended sampled decoding. DFlash2's mean accepted length was 4.39 on
HumanEval and 4.79 on MBPP, versus 3.91 and 3.99 for native MTP. At concurrency
one, HumanEval output throughput was 214.6 tok/s for DFlash2, 151.9 for MTP,
and 69.0 autoregressive; at concurrency 32 those figures were 1,799.0, 1,296.8,
and 1,546.5 tok/s. The last row is the warning: speculative decoding can lose
throughput once the target is compute-saturated, although DFlash2 held up better
than MTP in this experiment.
[DFlash2 model-card evaluation](https://huggingface.co/incoai/Qwen3.8-27B-DFlash2#evaluation)

A single GB10 submission in the SGLang tracker reported 39.4 tok/s for a
single-stream code fixture with DFlash2 versus 25.4 for its previous DSpark
default, plus 135–148 aggregate tok/s at concurrency eight and about 258 at
concurrency 32.
This is valuable reproducible field evidence, but it is one system and not
SGLang CI or a released support-matrix guarantee.
[SGLang GB10 measurements](https://github.com/sgl-project/sglang/issues/35860#issue-3362640469)

N-gram speculation has almost no model-weight/KV-cache cost and vLLM describes
its gain as low-to-medium, while model-based methods generally offer larger
latency gains. It is workload-sensitive and can be slower: an upstream vLLM
issue measured Qwen3-32B/ShareGPT with only 38.8% draft acceptance and worse
latency/throughput than the baseline across several N-gram settings. Conversely,
it should be strongest on deliberately copy-heavy edits. Measure that exact
distribution; do not extrapolate from generic chat.
[vLLM method guidance](https://github.com/vllm-project/vllm/blob/main/docs/features/speculative_decoding/README.md#method-selection-at-a-glance),
[vLLM Qwen N-gram slowdown report](https://github.com/vllm-project/vllm/issues/19254)

DFlash2 carries an approximately 2B-parameter BF16 drafter and its own cache,
so it consumes memory that would otherwise support KV capacity/concurrency. A
Blackwell workstation report measured 203.0 tok/s for DFlash2 versus 112.5 for
MTP, but the available KV pool fell from 515,501 to 359,511 tokens.
[vLLM Blackwell DFlash2 report](https://github.com/vllm-project/vllm/issues/53428#issue-3350383158)
At very long context, a separate vLLM 0.28.0 report found the drafter's full
context scan turned a short-context win into a major loss: roughly 71 tok/s
without speculation versus 16 tok/s with DFlash at 185K context, with no
per-sequence-length disable hook.
[vLLM long-context DFlash report](https://github.com/vllm-project/vllm/issues/54691#issue-3400322788)

## Quality and stability risks

Speculative decoding is intended to preserve the target distribution, and the
DFlash2 publisher explicitly claims greedy equivalence and distribution-
preserving sampling. Runtime integration bugs currently weaken that assurance:

- vLLM has an open Qwen3.8 DFlash2 report where greedy thinking-mode output
  diverges at token 30 even with one draft token, eager execution, and prefix
  caching disabled; thinking-disabled output matched.
  [vLLM issue #54928](https://github.com/vllm-project/vllm/issues/54928)
- vLLM has an open DFlash2/xgrammar report that returns valid JSON but repeatedly
  fails to advance the grammar FSM, causing retries and error logs.
  [vLLM issue #53777](https://github.com/vllm-project/vllm/issues/53777)
- SGLang has open reports covering concurrent DFlash state/context corruption,
  Qwen3.8 thinking-mode greedy divergence, ineffective deterministic behavior
  on GB10 NVFP4, and DFlash/prefill-CUDA-graph failures.
  [SGLang #36548](https://github.com/sgl-project/sglang/issues/36548),
  [#38009](https://github.com/sgl-project/sglang/issues/38009),
  [#36291](https://github.com/sgl-project/sglang/issues/36291),
  [#35437](https://github.com/sgl-project/sglang/issues/35437)
- A vLLM N-gram report on Qwen3-class tool calls found `prompt_lookup_min=2`
  produced malformed tool syntax in about half the requests; raising it to
  eight yielded 30/30 and 24/25 clean runs in two small experiments. Current
  vLLM main defaults both N-gram bounds to five, but tool/JSON parity still
  needs an explicit gate.
  [vLLM issue #40875](https://github.com/vllm-project/vllm/issues/40875),
  [current defaults](https://github.com/vllm-project/vllm/blob/e0aaef85f3de7a2376aee3708a3cba96286b41b1/vllm/config/speculative.py#L1156-L1185)

Enable DFlash2 selectively only after target-token parity, tool-call, structured
output, multi-turn cache, concurrency, and long-context tests pass on the pinned
GB10 tuple. Until the open thinking/state issues are resolved locally, prefer
thinking disabled for the DFlash2 profile and route schema-constrained or
high-integrity tool calls to the qualified MTP profile. A second service/router
can choose DFlash2 *or* N-gram per request class; that is routing, not composing
the algorithms.

## Exact configuration deltas

These snippets show only the speculative-decoding delta. Preserve and pin the
surrounding target model, revisions, parsers, cache settings, context, and
hardware-specific runtime image separately.

### vLLM native MTP (existing baseline)

The repository currently uses the deprecated model-specific alias. Current
vLLM normalizes that alias to `mtp`; the canonical form is:

```text
VLLM_SPECULATIVE_CONFIG='{"method":"mtp","num_speculative_tokens":3}'
```

The existing value remains accepted in current code:

```text
VLLM_SPECULATIVE_CONFIG='{"method":"qwen3_5_mtp","num_speculative_tokens":3}'
```

[vLLM MTP alias normalization](https://github.com/vllm-project/vllm/blob/e0aaef85f3de7a2376aee3708a3cba96286b41b1/vllm/config/speculative.py#L1105-L1109)

### vLLM DFlash2 (experimental same-engine control)

Use vLLM 0.28.0 or a later pinned revision that includes DFlash2, then replace
the MTP speculative config with:

```text
VLLM_SPECULATIVE_CONFIG='{"method":"dflash","model":"incoai/Qwen3.8-27B-DFlash2","revision":"dedf8df68adfb1afeaf7b7480c0a0243108177b4","num_speculative_tokens":7}'
```

The publisher's canonical DFlash2 command uses method `dflash` and seven draft
tokens (block size eight).
[DFlash2 quick start](https://huggingface.co/incoai/Qwen3.8-27B-DFlash2#quick-start)

### vLLM prompt lookup/N-gram (standalone, not combined)

Replace the entire speculative config with a conservative tool-oriented trial:

```text
VLLM_SPECULATIVE_CONFIG='{"method":"ngram","num_speculative_tokens":4,"prompt_lookup_min":8,"prompt_lookup_max":12}'
```

For a non-tool copy-heavy test, also sweep the current defaults (`5,5`) and
`5,10`. Lower minimums match more often but are more ambiguous; larger draft
blocks can improve copy-heavy output and waste more verification work when a
match is wrong. vLLM's documented generic example is:

```text
--speculative-config '{"method":"ngram","num_speculative_tokens":4,"prompt_lookup_min":2,"prompt_lookup_max":5}'
```

[vLLM N-gram schema and example](https://github.com/vllm-project/vllm/blob/main/docs/features/speculative_decoding/README.md#n-gram)

### SGLang DFlash2 (current performance profile)

```text
--speculative-algorithm DFLASH \
--speculative-draft-model-path incoai/Qwen3.8-27B-DFlash2 \
--speculative-draft-model-revision dedf8df68adfb1afeaf7b7480c0a0243108177b4 \
--speculative-draft-model-quantization unquant \
--speculative-num-draft-tokens 8
```

The exact GB10 field recipe additionally used FlashInfer, a 0.50 static memory
fraction, disabled prefill CUDA graphs, and several Mamba/cache controls. Treat
those as a pinned GB10 candidate tuple, not universal defaults.
[SGLang GB10 flags](https://github.com/sgl-project/sglang/issues/35860#issue-3362640469),
[SGLang validation/defaults](https://github.com/sgl-project/sglang/blob/main/python/sglang/srt/arg_groups/speculative_hook.py#L2484-L2690)

### SGLang NGRAM (standalone and different semantics)

```text
--speculative-algorithm NGRAM \
--speculative-num-draft-tokens 12 \
--speculative-ngram-min-bfs-breadth 1 \
--speculative-ngram-max-bfs-breadth 10 \
--speculative-ngram-max-trie-depth 18
```

An optional external corpus adds:

```text
--speculative-ngram-external-corpus-path /path/to/representative-code.jsonl \
--speculative-ngram-external-sam-budget <draft-node-count> \
--speculative-ngram-external-corpus-max-tokens <count>
```

NGRAM disables SGLang's overlap scheduler and mixed chunked prefill, does not
support DP attention, and may require FlashInfer when breadth is greater than
one with page sizes greater than one. These tradeoffs can erase its proposal
benefit under concurrency.
[SGLang speculative-decoding reference](https://github.com/sgl-project/sglang/blob/main/docs_new/docs/advanced_features/speculative_decoding.mdx),
[external corpus PR](https://github.com/sgl-project/sglang/pull/21425)

There is **no valid flag set** for either of these forms:

```text
DFlash2 + NGRAM
MTP + NGRAM
```

## Small GB10 benchmark plan

Keep model/tokenizer/chat-template revisions, parser settings, request bodies,
context limit, prefix-cache policy, KV dtype, and sampling identical. Pin and
record the complete runtime/container commit because the comparison otherwise
mixes algorithm and runtime effects.

### Arms

| Arm | Runtime and speculative setting | Disposition |
| --- | --- | --- |
| 1 | Current vLLM + native MTP, `mtp`, K=3 | Required baseline |
| 2 | Pinned SGLang + DFlash2, block=8 | Candidate performance profile |
| 3 | DFlash2 + prompt/N-gram | Record **unsupported / not run** |
| 3a | vLLM N-gram only, K=4, bounds `8..12` plus `5..5` sweep | Optional exploratory substitute |
| 3b | Policy envelope: route each fixture class to the better of arm 2 or 3a | Optional; this measures selection, not composition |

If time permits, add vLLM 0.28 DFlash2 as a same-runtime control. It separates
the DFlash2 effect from vLLM-versus-SGLang scheduler/kernel differences.

### Fixtures

Use at least five fixed, replayable tasks, 10 warm repetitions each:

1. fresh code generation with little prompt copying;
2. a high-copy mechanical refactor where the complete source file is in the
   prompt and most output lines remain unchanged;
3. a semantic multi-file refactor with moderate copying;
4. a multi-turn agent trace that repeats a 16K–64K repository prefix;
5. tool calls and JSON-schema output, both thinking disabled and enabled.

Run context buckets near 8K, 32K, and 64K at concurrency 1, 4, and 8. Add one
128K/long-context probe before any promotion. Replay an identical long-prefix
request twice so prefix-cache hits and second-request TTFT are measured, not
assumed.

### Measurements and gates

- output tok/s, median/P95 inter-token latency, TTFT, and task wall time;
- mean acceptance length, per-position acceptance, drafted/accepted tokens;
- prefix-cache hit tokens and second-request TTFT;
- peak unified memory, KV capacity, power, and any OOM/restart;
- exact generated-token parity at temperature zero where expected;
- compile/test success, patch applicability, tool-call parse success, JSON
  schema validity, and reviewer-scored semantic correctness;
- a 10K-request mixed sampling/greedy soak after the small matrix passes.

Promote a profile only if successful-task wall time improves—not merely raw
tok/s—without a correctness, tool, cache, memory, or stability regression.

## Recommendation

1. **Keep vLLM/native MTP as the qualified baseline.** Pin a version where the
   repeated-prefix cache gate passes on GB10.
2. **Continue SGLang/DFlash2 only as an isolated candidate profile.** Its H200
   and early GB10 performance is strong, especially for code, but the current
   open thinking/concurrency/graph issues do not justify making it universal.
3. **Do not implement or advertise DFlash2 + prompt/N-gram lookup.** No stock
   runtime supports it.
4. If copy-heavy refactoring is important, benchmark **vLLM N-gram alone** as
   arm 3a and compare it with DFlash2. Select between separate endpoints by
   workload only after enough real tasks show a stable boundary. SGLang NGRAM
   with an external code corpus is a lower-priority experiment because it is
   not prompt lookup and disables useful scheduler paths.
5. Revisit composition only when an upstream runtime ships a documented hybrid
   proposer with tests, a released build, and GB10 evidence. A roadmap item or
   custom patch is not mature enough for this deployment.
