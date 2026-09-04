# Best model choices for HomeCompute on one GB10

Verified against live primary sources: 2026-09-05.

Scope: the repository's 128 GB NVIDIA GB10 / DGX Spark-class Home Spark.
“HB10” is interpreted as GB10. The repository says the appliance is not yet
available, so these are evidence-ranked recommendations, not local test winners.

## Text, coding, reasoning, and agents

| Use case | First choice | Why and remaining qualification |
| --- | --- | --- |
| Best overall local assistant, research, reasoning, summaries | **Qwen3.8-27B FP8** | Strongest combined quality evidence among the comfortably fitting candidates audited. Start with publisher FP8 and 32K context; qualify GB10 kernels, latency, and longer context. |
| Complex coding, repository work, Codex/Responses | **Qwen3.8-27B FP8**, with **Ornith-1.5-35B-A3B Q8_0 or Q6_K** as the focused challenger | Qwen has the stronger broad current evidence. Ornith is a credible specialist whose sparse architecture could improve completion time. Their different benchmark harnesses do not establish a definitive ranking. |
| Fast tool agents and background work | **NVIDIA Nemotron 3.5 Lightning 30B-A3B NVFP4 + DSpark** | Most explicit current publisher-supported single-GB10 performance route. Measure target-only before enabling the draft and choose on successful task completion time. |
| Danish Home Assistant conversation | **Qwen3.8-27B with thinking disabled**, initially sharing the general service | Avoid adding a resident model without evidence that it improves home interaction. No audited publisher source proves a Danish home-control winner. |
| Danish/multilingual fallback | **Gemma 4 31B IT NVFP4** | Broad multilingual model with official Spark support; evaluate it if Qwen fails Danish names, ambiguity, instructions, or short-turn latency. Gemma 4 26B-A4B is an additional sparse alternative if latency is the specific gap. |

These are judgments from the evidence below. For Home Assistant, success means
correct Danish understanding and tool arguments plus prompt response, not a
coding-benchmark score. Home Assistant should continue to validate and execute
actions itself. OpenAI-compatible chat/tool serving also does not establish the
repository's Responses API and Codex compatibility without its protocol tests.

### Qwen3.8-27B: strongest quality-first default

The publisher reports 73.0 Terminal-Bench 2.1, 61.7 SWE-bench Pro, 42.2
DeepSWE 1.1, 79.5 IFBench, and 89.2 GPQA Diamond. It supplies a dense 27B
multimodal checkpoint with 262,144 native context, tools, controllable reasoning,
and an MTP head. These are publisher results, often using 256K context; smaller
local context and different harnesses can change them. The FP8 card says its
quality is nearly identical to the original checkpoint.
[Publisher card](https://huggingface.co/Qwen/Qwen3.8-27B-FP8).

Qwen's re-evaluated Muse comparison is particularly useful: SWE-bench Pro is
61.7 versus 51.2 using the same Claude Code harness for those models. Its
table also gives Terminal-Bench 73.0 versus 51.7 and IFBench 79.5 versus 77.0.
That supports selecting Qwen over Muse for the general coding/agent role,
without asserting a corresponding lead on unmeasured Danish home tasks.
[Comparison and harness notes](https://huggingface.co/Qwen/Qwen3.8-27B-FP8).

The official runtime recipe verifies several Blackwell configurations and
documents Qwen parsers and MTP. Its measured examples include GB300 and RTX
5090; those are not an exact GB10 qualification. It also documents third-party
NVFP4 conversions. Prefer official FP8 as the quality reference before trading
precision for speed.
[vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B).

### Nemotron: best-supported performance candidate

NVIDIA explicitly documents one DGX Spark, vLLM 0.27.1, the DSpark draft,
`nemotron_v3` reasoning parser, and `qwen3_coder` tools parser. On GB10 the
NVFP4 weights execute through **W4A16 Marlin**, not native FP4 tensor-core
compute. NVIDIA recommends DSpark for low-concurrency and latency-sensitive
work. Its six listed supported post-training languages omit Danish, so this
should not automatically become the home voice model. License: OpenMDW-1.1.
[NVIDIA card and GB10 recipe](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4).

### Correction to yesterday's research: official Ornith quants exist

The earlier shortlist says there is no official low-precision Ornith derivative.
That is incorrect: the publisher hosts **Ornith-1.5-35B-A3B-GGUF**, including
Q4_K_M, Q5_K_M, Q6_K, and Q8_0. Q8_0 is a reasonable quality-first coding
comparison at this memory capacity; Q6_K is the lower-memory challenger. No
quant-specific quality preservation or exact GB10 recipe was established here.
The model card's hand-written serving section still describes the BF16 model
on two 80 GB GPUs. Hugging Face's auto-generated library examples are not
evidence that every listed backend works with these GGUF files.
[Publisher GGUF card](https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B-GGUF).

Ornith reports 67.8 Terminal-Bench 2.1 with Terminus-2, 59.6 SWE-bench Pro,
79 SWE-bench Verified, and 89.2 GPQA. Its SWE results use OpenHands while
Qwen's Pro results use Claude Code, so do not interpret their numerical
difference as a controlled head-to-head.
[Publisher benchmark details](https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B).

### Gemma and other alternatives

Google documents more than 140 languages, native function calling, configurable
thinking, and both dense and sparse Gemma 4 variants. NVIDIA explicitly lists
31B NVFP4 and 26B-A4B among supported Spark models. This establishes a
credible multilingual comparison, not Danish supremacy.
[Google card](https://huggingface.co/google/gemma-4-26B-A4B-it),
[NVIDIA Spark matrix](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md#model-support-matrix).

Muse Glimmer 30B NVFP4 remains an optional dense agent/vision comparison with
NVIDIA's Spark support and runtime instructions, but does not displace Qwen on
the current evidence. Qwen3.6 NVFP4 remains a useful integration baseline;
being the easiest first deployment does not make it the highest-quality model.
[Muse card](https://huggingface.co/nvidia/Muse-Glimmer-30B-NVFP4),
[Qwen3.6 card](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4).

## Verified artifact sizes and fit boundaries

Sizes below are decimal GB, summed from weight files in the official Hugging
Face model API on the verification date. They exclude runtime workspaces,
KV/recurrent caches, OS/display, other services, and recovery headroom. A model
fitting by weight size does not guarantee a working runtime or usable latency.

| Artifact | Weight GB | Revision / fit interpretation |
| --- | ---: | --- |
| Qwen3.8-27B FP8 | 30.867 | `017b9c7af6b5689d5dd426a76e0bc077eb5ca20a`; comfortable weight budget, exact GB10 tuple still needs qualification |
| Nemotron 3.5 Lightning NVFP4 | 21.562 | `cc84af2fe71647d87f4486c064f320e1e7535243`; explicit GB10 execution, draft is additional |
| Ornith 1.5 35B Q8_0 GGUF | 37.802 | `12393612fd4f730ff5aadc23e9b8f9648aa49ceb`; publisher artifact, GB10 runtime unqualified |
| Ornith 1.5 35B Q6_K GGUF | 29.209 | Same GGUF revision |
| Ornith 1.5 35B Q4_K_M GGUF | 21.713 | Same GGUF revision; optional vision projector adds 0.903 GB |
| Gemma 4 31B IT NVFP4 | 32.633 | `4135a98a9b728a548947683219633b25682223ac`; supported in Spark matrix |
| Qwen3-Coder-Next FP8 | 80.381 | `da6e2ed27304dd39abadd9c82ef50e8de67bdd4c`; plausible reduced-context fit, much less headroom and no verified advantage over first choices |
| NVIDIA Qwen3.8-Flash-Next NVFP4 | 132.680 | `fab0aecb760cec45227f6656abcaafa11abca87a`; reject for shared production residency: insufficient memory headroom even before runtime |

Sources: [Qwen FP8 API](https://huggingface.co/api/models/Qwen/Qwen3.8-27B-FP8?blobs=true),
[Nemotron API](https://huggingface.co/api/models/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4?blobs=true),
[Ornith GGUF API](https://huggingface.co/api/models/ornith-ai/Ornith-1.5-35B-A3B-GGUF?blobs=true),
[Gemma API](https://huggingface.co/api/models/nvidia/Gemma-4-31B-IT-NVFP4?blobs=true),
[Coder-Next API](https://huggingface.co/api/models/Qwen/Qwen3-Coder-Next-FP8?blobs=true),
[Flash-Next API](https://huggingface.co/api/models/nvidia/Qwen3.8-Flash-Next-NVFP4?blobs=true).

Do not sum every alternative file in a GGUF repository: select one quantization.
Likewise, active MoE parameter count describes per-token computation, not total
weight residency. The NVIDIA Flash-Next artifact is about 123.6 GiB; even if
the physical 128 GB label means 128 GiB, its remainder is inadequate for this
appliance's shared-service headroom policy. A community conversion running at
lower precision would establish a different, experimental candidate.

## Speech and retrieval recommendations

The following are recommendations for evaluation on GB10, not measured GB10 results.
Small parameter counts make these services plausible memory fits; their ARM64
packages, CUDA kernels, adapters, and combined resource use still need validation.

### Danish speech recognition: expand the existing shortlist

For Danish conversational meetings, choose `CoRal-project/roest-v3-whisper-1.5b`
as the first quality candidate, with `syvai/hviske-v5.3` as the strongest
conversation-specific challenger found in this audit. Stock Whisper is a
multilingual reference, not the assumed Danish accuracy ceiling.

CoRal reports conversational CER of 11.6% against stock Whisper large-v3's
27.5% on its evaluation. This is a publisher result, not a Plaud recording test.
The model has a standard Transformers inference path and custom OpenRAIL-derived
terms. [CoRal model card](https://huggingface.co/CoRal-project/roest-v3-whisper-1.5b)

The independently operated Open Danish ASR Leaderboard publishes raw outputs,
a common scoring procedure, and results across five datasets. Its live results
API on 2026-09-05 returned:

| Model | Mean WER across five datasets | Conversational CoRal WER |
| --- | ---: | ---: |
| Hviske v5 | 13.60% | 25.43% |
| Røst v3 Whisper 1.5B | 13.92% | 21.08% |
| Hviske v5 tiny | 14.12% | 26.12% |
| Hviske v5.3 | 14.42% | 19.68% |
| Danish Parakeet RNNT 110M | 19.92% | 50.87% |
| Whisper large-v3 | 24.05% | 49.51% |
| Whisper large-v3-turbo | 28.59% | 63.83% |
| Parakeet TDT 0.6B v3 | 30.81% | 50.96% |

These are results from that benchmark's own harness, not comparable to numbers
using different normalization, and not GB10 latency measurements. The ordering
changes by domain: Hviske v5 has the lowest aggregate WER among these open
models, while v5.3 leads the conversation column. Household entities,
code-switching, negation, timestamps, and noisy long meetings need local tests.
[Benchmark dataset, methodology, and raw outputs](https://huggingface.co/datasets/RyeAI/danish-asr-leaderboard),
[results API](https://datasets-server.huggingface.co/rows?dataset=RyeAI/danish-asr-leaderboard&config=results&split=leaderboard&offset=0&length=100)

Hviske v5.3 is a 2B model with custom-code Transformers and vLLM paths. Its
CC-BY-NC-4.0 terms matter for the repository's work-meeting use case; do not
silently treat it as the shared work/personal default.
[Hviske v5.3 card](https://huggingface.co/syvai/hviske-v5.3)

For short personal Danish commands, add `syvai/hviske-v5-tiny` as a
quality/latency challenger to Danish Parakeet RNNT 110M. It is 263M, has CUDA,
ONNX, and GGUF implementations, but is gated, noncommercial, Danish-only, and
offline without timestamps. Its publisher's RTX 3090 speed figures do not
establish GB10 speed. [Tiny card](https://huggingface.co/syvai/hviske-v5-tiny)

Keep `openai/whisper-large-v3-turbo` for the first maintained Home Assistant
integration and mixed-language baseline. NVIDIA's Danish RNNT model explicitly
lists Blackwell but is not supported by Riva; its adapter still needs ownership.
Parakeet TDT v3 supports Danish and English, punctuation, and timestamps, making
it a useful throughput comparison, but the Danish benchmark above does not
support making it the accuracy winner.
[Home Assistant Whisper app](https://github.com/home-assistant/addons/blob/master/whisper/DOCS.md),
[Danish RNNT card](https://huggingface.co/nvidia/parakeet-rnnt-110m-da-dk),
[TDT v3 card](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)

### Diarization

Use `pyannote/speaker-diarization-community-1` for recorded meetings. It can
run locally on CPU/CUDA after gated download and supplies exclusive diarization
to simplify transcript alignment. Validate speaker assignment on overlapping
Danish/English Plaud audio; it does not itself transcribe words.
[Community-1 card](https://huggingface.co/pyannote/speaker-diarization-community-1)

### Danish speech output and podcasts

- **Interactive naturalness candidate:** `CoRal-project/roest-v3-chatterbox-350m`.
  Publisher MOS is 4.01 from a small native-Danish panel; GB10 first-audio latency
  and pronunciation remain unmeasured.
  [350M card](https://huggingface.co/CoRal-project/roest-v3-chatterbox-350m)
- **Danish voice quality candidate:** `CoRal-project/roest-v3-chatterbox-500m`.
  Publisher MOS is 4.23. This is a reason to audition it, not proof that the
  0.22 difference is statistically significant or that it wins an hour-long
  podcast. [500M card](https://huggingface.co/CoRal-project/roest-v3-chatterbox-500m)
- **Danish podcast implementation candidate:** `ResembleAI/chatterbox`,
  explicitly loading Multilingual V3. It supports Danish and English, is MIT,
  and has an upstream CUDA path. Generate individual turns and verify voice
  consistency across an episode. Compare its listening quality with Røst 500M.
  [Chatterbox card](https://huggingface.co/ResembleAI/chatterbox)
- **English podcast turns:** `Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice` for supplied
  voices; the Base sibling for reference-conditioned voices. Qwen's language
  list excludes Danish. [Qwen TTS card](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice)
- **Operational home fallback:** Piper `da_DK-talesyntese-medium` on CPU,
  preferably outside the GB10 failure domain. Its maintained Home Assistant
  integration is the reason to retain it; it is not the voice-quality winner.
  [Piper integration](https://www.home-assistant.io/integrations/piper/),
  [Danish voice card](https://huggingface.co/rhasspy/piper-voices/blob/main/da/da_DK/talesyntese/medium/MODEL_CARD)

The CoRal Chatterbox fork is archived; its live page reports September 3, 2026,
rather than the July 3 date in the older repository note. Røst needs a maintained
service adapter and exact weight-license qualification before shared production
use. [CoRal fork](https://github.com/alexandrainst/coral_chatterbox)

### Private retrieval

For the user's quality-first request, benchmark **Qwen3-Embedding-8B plus
Qwen3-Reranker-4B**, comparing Reranker-8B on the actual corpus. The embedding
family supports multilingual/code retrieval and adjustable dimensions. The
reranker publisher's table does not give 8B a uniform win over 4B; choosing both
8B models simply because they are larger is unsupported.
[Embedding card](https://huggingface.co/Qwen/Qwen3-Embedding-8B),
[reranker comparison](https://huggingface.co/Qwen/Qwen3-Reranker-4B)

Approximate BF16 parameter-only storage is 16 GB + 8 GB for this pair, before
runtime and batching. These are estimates, not downloaded artifact measurements.
Load the embedding worker for ingestion and avoid forcing the entire retrieval
stack to remain resident during coding. Qwen's 0.6B pair remains the efficient
control; it is not established as the quality ceiling.

Jina embeddings v5 text small is a newer 0.6B multilingual efficiency challenger,
but its noncommercial terms make it a separate consideration for the work
corpus. No evidence here establishes a Danish corpus winner over Qwen.
[Jina model card](https://huggingface.co/jinaai/jina-embeddings-v5-text-small)

## Recommended operating shape for this repository

### Can all of these models run at the same time?

They may all be **installed on disk**, but they should not all be **loaded and
serving concurrently** on one GB10. The recommended steady state is one shared
Qwen3.8 process plus the selected small voice services. Gateway aliases can
share that one process, so five text routes do not require five copies of the
model.

Keep Nemotron, Ornith, Gemma, and other full LLM challengers stopped until a
benchmark or scheduled workload needs them. Prefer loading embeddings and the
reranker for ingestion/query windows initially. Qwen3.8 plus Nemotron plus
Ornith consumes roughly 82--90 GB in selected weight files alone, before the
Nemotron draft, caches, runtime workspaces, audio services, and OS. That is not
a safe default under the repository's 10% measured-headroom requirement, and
even a memory-fitting combination still shares one GPU and its memory bandwidth.

The first coexistence target to prove is:

`Qwen3.8-27B FP8 + chosen STT + chosen TTS + pyannote + telemetry`

Only after that passes the required simultaneous Codex, background request,
meeting transcription, and Home Assistant voice test should another resident
model be added.

My selection hypothesis is one shared Qwen3.8-27B FP8 process for `coding`,
`automation`, `research`, `meeting` summaries, and `assistant`; compare
Nemotron for faster English agent jobs and Ornith for difficult coding, loading
challengers serially. Keep ordinary home commands on deterministic Assist,
and test Qwen3.6 NVFP4 for conversational `home`. If that cannot meet mixed-load
voice latency, benchmark a reserved small `Qwen/Qwen3.5-4B` process with thinking
disabled. Its Danish tool accuracy is a hypothesis, not established evidence.
[Small Qwen card](https://huggingface.co/Qwen/Qwen3.5-4B)

Keep `home-core` as the gateway/application/state host and GB10 as inference.
Prioritize voice over ingest, podcast generation, and batch meeting work.
Separate processes reserve memory but do not isolate GPU compute or bandwidth;
queueing and admission control remain necessary.

Start text qualification at 32K, then test the shared model at 64K for Hermes.
Use realistic filled contexts, not merely a configured maximum. Keep at least
10% of OS-reported unified memory available under the actual mixed load, including
OS, weights, KV caches, workspaces, speech services, and telemetry. Avoid assigning
90% of the GPU-visible pool independently to several vLLM servers.
[Repository requirements](../requirements.md),
[current compute profile](../../config/compute-node.env.example)

Acceptance should compare: successful tested Codex changes; Danish tool/entity
accuracy and spoken latency; faithful cited summaries; correct Hermes long tool
loops; Danish ASR entity errors and word-to-speaker alignment; and blinded TTS
pronunciation/naturalness. The repo requires home p95 TTFT <=750 ms, first audible
confirmation <=2.5 seconds, and TTS first audio <=750 ms while other work is active.
No source inspected proves that an untested model stack meets those limits.

This research changes candidate priorities, not production selection.
No weights were downloaded, no target machine was benchmarked, and no serving
configuration was changed.
