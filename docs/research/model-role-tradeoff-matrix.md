# Model-role trade-off matrix

Verified: 2026-09-05

Status: scorecard and switch-rule reference. Candidate dispositions in the
dated role detail are superseded by the
[current shortlist](llm-installation-recommendation.md): the mandatory text
wave is Qwen3.6, Qwen3.8-27B, and Nemotron 3.5; Ornith, Muse, and Gemma are
conditional, and GPT-OSS, Devstral Small 2, and GLM-4.7-Flash are outside the
normal queue.

## Decision in brief

Several roles do have a genuine model choice, and the non-selected candidates
should remain visible with their purpose and promotion condition. The shortlist
should not, however, become a flat list in which every model is tested for every
role.

- **Must-run** means the candidate answers a distinct, unresolved decision and
  belongs in the first benchmark wave.
- **Conditional** means run it only after the named baseline misses a repository
  gate or a distinct higher-quality tier is needed.
- **Optional control** means it isolates precision, memory, runtime, or quality;
  it is not another presumptive production winner.

The provisional selections are:

| Role | Provisional recommendation | How uncertain is it? |
| --- | --- | --- |
| Shared Danish `automation`, `research`, and `meeting` text work | Qwen3.6 integration, then `Qwen/Qwen3.8-27B-FP8` quality qualification | Qwen3.8 is the first quality hypothesis; Danish and workflow tests remain decisive |
| Home Assistant `home` | Shared Qwen3.6 first | Genuinely open if mixed-load voice latency fails; then a reserved small model may win |
| Codex `coding` | Qwen3.8-27B quality candidate after Qwen3.6 integration | Open against Nemotron performance; Ornith is conditional on a measured gap |
| Hermes `assistant` | Qwen3.8 after Qwen3.6 integration, in a 64K-or-greater profile | Acceptance at 64K and mixed load is unresolved |
| Serialized high-capacity reasoning | No mandatory separate model | GPT-OSS-120B is only an explicit one-time Harmony/MXFP4 control |
| Home STT | Whisper large-v3-turbo operationally; Hviske v5 tiny and Danish Parakeet as adapter-dependent latency challengers | Open on real far-field, entity, code-switched speech, licensing, and end-to-end integration |
| Meeting STT | Røst v3 Whisper 1.5B first for Danish accuracy; Hviske v5.3 for personal conversational use; Whisper Turbo as the integration/multilingual control | The common Danish benchmark favors Røst/Hviske, but the home and meeting winners may differ and Hviske is noncommercial |
| Danish TTS | Piper operational baseline; Røst 350M promotion candidate | Genuinely open on pronunciation, naturalness, integration, and latency |
| Diarization | `pyannote/speaker-diarization-community-1` for recorded meetings | NVIDIA Streaming Sortformer is a distinct conditional live/low-latency challenger; numerical acceptance thresholds are still missing |
| Embeddings/reranking | Deferred until a real corpus; Qwen3 0.6B pair as efficiency baseline and 8B embedding plus 4B reranker as quality-first candidate | Danish/private retrieval quality and resident-memory cost remain open |

## Two rules that apply to every text candidate

First, **publisher multilingual claims are not Danish acceptance evidence**.
The repository requires Danish names, dates, structured output, factuality,
Home Assistant tools, code-switching, and household audio. No cited publisher
card reports those complete workload results. Nemotron 3.5 is a particularly
important distinction: its card says Danish appeared in multilingual pretraining,
but its supported post-training language list names English, Spanish, French,
German, Italian, Japanese, and coding languages—not Danish.
[Nemotron 3.5 model card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4#model-summary)

Second, **an `NVFP4` filename does not prove native GB10 FP4 Tensor Core
execution**. NVIDIA's exact Nemotron 3.5 table says that the checkpoint is stored
as NVFP4 but runs W4A16 through Marlin on DGX Spark/GB10, with no native FP4
Tensor Core path. NVIDIA's Qwen3.6 Spark command likewise selects a Marlin MoE
backend. Each benchmark record must capture the component layout, activation
and KV precision, runtime, and actual selected kernels. FP8 and MXFP4 artifacts
are valid candidates; they are not automatically inferior because their names
do not say NVFP4.
[Nemotron 3.5 hardware matrix](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4#model-summary),
[NVIDIA Qwen3.6 Spark command](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4#usage)

## 1. Shared Danish automation, research, and meeting text

These three aliases share Danish/English synthesis, factuality, schema, privacy,
and mixed-load requirements. Meeting summarization adds provenance and a
versioned decisions/actions schema; it does not make the text model the owner of
STT or diarization.

| Tier | Candidate | Pros | Cons and risks |
| --- | --- | --- | --- |
| **Must-run; recommended baseline** | `nvidia/Qwen3.6-35B-A3B-NVFP4` | One exact NVIDIA single-Spark vLLM recipe; 35B total/3B active; 262K context; tools, reasoning parser, FP8 KV, and optional MTP; one shared process exercises all aliases. NVIDIA reports close BF16/NVFP4 results on its chosen aggregate benchmarks. | No publisher result establishes the repository's Danish, source-bounded research, Aula, or meeting-schema gates. The Spark recipe uses Marlin, so NVFP4 storage is not enough to claim native W4A4. [NVIDIA card](https://huggingface.co/nvidia/Qwen3.6-35B-A3B-NVFP4) |
| **Must-run quality candidate** | `Qwen/Qwen3.8-27B-FP8` | Newer dense Qwen quality hypothesis; official publisher FP8 checkpoint; 262K context and reasoning/tool features. | No Qwen/NVIDIA-published NVFP4 checkpoint or exact one-Spark recipe was found. Dense FP8 residency and the required recent runtime may cost more operationally; Danish task results remain local work. [Qwen card](https://huggingface.co/Qwen/Qwen3.8-27B-FP8) |
| **Must-run performance candidate** | `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` | Exact one-Spark target plus DSpark recipe; 30B/3B active; configurable reasoning and strong first-party agent/coding focus. | Supported post-training list omits Danish; OpenMDW-1.1 needs acceptance; strict-format owner results are imperfect. Do not promote it for Danish work from English agent benchmarks. [NVIDIA card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4) |
| **Conditional multilingual control** | `nvidia/Gemma-4-31B-IT-NVFP4` | Dense quality control; 256K context, multimodal input, function calling, Apache-2.0; NVIDIA-published ModelOpt artifact listed in the Spark vLLM matrix. | Dense 31B may cost more active compute and mixed-load headroom. Run only if the mandatory wave exposes a Danish/multilingual gap. [NVIDIA card](https://huggingface.co/nvidia/Gemma-4-31B-IT-NVFP4) |
| **Optional one-time protocol control** | `openai/gpt-oss-120b` | Native MXFP4 and Harmony provide a distinct integration comparison. | Large residency and Harmony-specific surface add no default role. Run only when that comparison is explicitly desired. [OpenAI card](https://huggingface.co/openai/gpt-oss-120b) |
| **Optional precision control** | `nvidia/Qwen3.6-27B-NVFP4` | NVIDIA-published dense NVFP4 artifact can isolate an NVFP4-versus-FP8/runtime question near Qwen3.8's size. | It is Qwen3.6 rather than the newer Qwen3.8 model, so it is not a clean model-quality comparison; no current first-party one-Spark recipe was found; declared W4A16 does not prove native W4A4. [NVIDIA card](https://huggingface.co/nvidia/Qwen3.6-27B-NVFP4) |

**Why Qwen3.6 is provisional first choice.** It gives the broadest integration
coverage with the lowest shortlist complexity and an exact NVIDIA Spark recipe.
That is stronger day-one evidence than newer-model quality claims without a
pinned appliance path. It remains only a hypothesis until Danish and workflow
fixtures pass.

**Exact switch rule.** Keep Qwen3.6 if it passes at least 95% of schema fixtures,
90% of factual/instruction rubric points, the meeting factuality/schema suite,
the privacy/log canary, and the required mixed-load/memory gates. Select Gemma
or Qwen3.8 only if it passes every same hard gate and has the highest predeclared
role score on the identical fixtures. Run Nemotron 3.5 for a Danish alias only if
it independently passes that full Danish suite; English agent results do not
qualify it. Escalate a job to a 120B reasoner only when the shared winner fails a
pre-labelled difficult fixture and the reasoner passes it without violating
latency, privacy, and memory policy.

The repository currently gives numerical quality gates to `automation`, but not
equally explicit gates to `research` or meeting cleanup/summarization. Those
alias-specific rubrics must be frozen before the phrase "highest role score" can
produce an auditable winner.

## 2. Home Assistant

| Tier | Candidate | Pros | Cons and risks |
| --- | --- | --- | --- |
| **Must-run; recommended baseline** | Shared `nvidia/Qwen3.6-35B-A3B-NVFP4` | Avoids a second resident model; multilingual/tool-oriented; exact Spark recipe; tests the simplest architecture first. | May miss 750 ms p95 TTFT during Codex/background load. Publisher evidence does not establish exact Danish HA tools/entities. |
| **Conditional small quality candidate** | `Qwen/Qwen3.5-4B` | Current dense 4B model; publisher claims 201-language/dialect coverage and agent/tool capability; likely much smaller residency. | No first-party NVIDIA NVFP4 or Spark recipe is established here; multilingual aggregate evidence is not Danish HA accuracy; smaller capacity may lose on 100+ entities and negation. [Qwen card](https://huggingface.co/Qwen/Qwen3.5-4B) |
| **Conditional optimized controls** | `nvidia/Qwen3-8B-NVFP4`, then `nvidia/Qwen3-14B-NVFP4` | NVIDIA-published pre-quantized checkpoints listed in the Spark vLLM matrix; credible reserved-process GB10 candidates. The 8B/14B pair exposes the quality-versus-residency trade-off. | Older model generation; no local Danish tool evidence; an extra resident process consumes headroom even when idle. NVFP4 does not alone prove the best GB10 kernel. [8B card](https://huggingface.co/nvidia/Qwen3-8B-NVFP4), [14B card](https://huggingface.co/nvidia/Qwen3-14B-NVFP4), [Spark matrix](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md#model-support-matrix) |

**Why the shared model is provisional first choice.** A reserved model should
solve a measured latency/capacity failure, not exist merely because Home
Assistant is an important role. Home Assistant still tries deterministic Assist
intents first and remains the execution authority.
[Home Assistant LLM API](https://developers.home-assistant.io/docs/core/llm/),
[Assist best practices](https://www.home-assistant.io/voice_control/best_practices/)

**Exact switch rule.** Run small candidates only if shared Qwen3.6 misses p95
model TTFT of 750 ms, p95 first audible confirmation of 2.5 s, either threshold
under the required Codex-plus-background mixed load, or 10% measured memory
headroom. A replacement must then pass at least 98% exact normal tool/entity/
argument fixtures, 100% safety-critical denials, zero hallucinated entity
executions, all 10/25/50/100+ entity sets, both latency gates, and the memory
gate. If more than one alternative passes, choose the smallest/simplest exact
artifact-runtime tuple that meets every gate; do not trade a safety or quality
pass for lower latency.

## 3. Coding through Codex

| Tier | Candidate | Pros | Cons and risks |
| --- | --- | --- | --- |
| **Must-run quality candidate** | `Qwen/Qwen3.8-27B-FP8` | Current dense Qwen with strong coding/agent publisher evidence and positive owner structured-output tests. | Requires a recent pinned runtime and exact local Responses/tool qualification. [Qwen FP8 card](https://huggingface.co/Qwen/Qwen3.8-27B-FP8) |
| **Must-run agent/performance challenger** | `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` target-only, native MTP, and target+DSpark | Exact GB10 command and 967M DSpark draft; 30B/3B active; NVIDIA reports agent/coding evaluations; DSpark directly tests lower interactive latency. | Three tuples increase test cost. The target uses W4A16/Marlin on GB10; OpenMDW-1.1 needs approval; its supported natural-language list omits Danish. DSpark is speculative decoding, not another model or FP4 format. [target](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4), [draft](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4-DSpark) |
| **Conditional coding challenger** | `ornith-ai/Ornith-1.5-35B-A3B-GGUF` at Q8_0 or Q6_K | Strong publisher coding/agent results; publisher-hosted Q8_0 is 37.8 GB and Q6_K is 29.2 GB. | No exact Spark recipe or quant-specific quality result, plus conflicting real-codebase reports. Run only if Qwen3.8 leaves a gap. [publisher GGUF](https://huggingface.co/ornith-ai/Ornith-1.5-35B-A3B-GGUF) |
| **Optional specialist control** | `Qwen/Qwen3-Coder-Next-FP8` | Purpose-built for coding agents and long-horizon tool use. | Roughly 80 GB, no exact Spark recipe, and displayed publisher results used BF16. |

**Why Qwen3.8 goes first for quality.** It is substantially smaller than
Coder-Next and has stronger current general/coding evidence. Nemotron has the
strongest exact GB10 performance path. Ornith and Coder-Next are useful only if
the mandatory wave leaves a specific unresolved coding gap.

**Exact switch rule.** Reject any tuple that fails the Codex Responses/tool loop,
license review, or 10% mixed-load memory headroom. A candidate is promotable only
if at least 70% of the pinned .NET, Python, Vue, and React/TypeScript tasks pass
build/tests and frontier review without cloud reimplementation. Among passing
tuples, use the repository's predeclared weighted coding scorecard; select
Nemotron/DSpark only if its complete-task score remains competitive with
Qwen3.8 while materially improving latency. Add Ornith only if Qwen3.8 fails a
predeclared coding class and Ornith passes it.
Speculative decoding wins only on end-to-end accepted task time, not tokens/s.

## 4. Hermes personal assistant

| Tier | Candidate | Pros | Cons and risks |
| --- | --- | --- | --- |
| **Must-run; recommended path** | `nvidia/Qwen3.6-35B-A3B-NVFP4` at 64K or greater | Reuses the shared, already-qualified gateway and is the documented Spark-oriented NemoClaw managed-vLLM path; broad multilingual/tool hypothesis; 262K native context. | The 32K Phase C server cannot qualify Hermes. Three sessions at 64K can create KV/memory pressure and delay P0 voice. Model choice does not provide profile isolation or action authorization. [NemoClaw provider guide](https://docs.nvidia.com/nemoclaw/user-guide/hermes/inference/learn-and-choose/choose-inference-provider) |
| **Conditional English-agent challenger** | Nemotron 3.5 Lightning + DSpark | Strong exact Spark agent path, 1M validated context, speculative-decoding option, and low active parameter count. | Danish is outside the supported post-training list; different parsers/license/runtime; 0.85 memory utilization in the example is not a safe production budget; still needs the complete Hermes protocol and profile workload. |

**Why Qwen3.6 is provisional first choice.** It minimizes integration variables:
Hermes can first prove its sandbox, endpoint, persistence, and tool behavior
against the same model path as the other aliases. The model is not a security
boundary; OpenShell sandboxes, profile routing, data authorization, and human
approval are separate gates.

**Exact switch rule.** Qwen3.6 qualifies only in a pinned 64K-or-greater profile
that passes the Hermes OpenAI-compatible tool loop, persistence/rollback, and
three-session M6 mixed-load case while preserving P0 `home` latency and 10%
memory headroom. Test Nemotron only if Qwen3.6 fails that capacity/latency gate
or the English agent scorecard. Promote Nemotron only if it passes all the same
gates and every language actually assigned to `assistant`; its publisher's
English-agent results cannot qualify Danish sessions.

## 5. Optional serialized protocol control

| Tier | Candidate | Pros | Cons and risks |
| --- | --- | --- | --- |
| **Optional one-time control** | `openai/gpt-oss-120b` in native MXFP4 | Distinct Harmony/MXFP4 protocol path and adjustable reasoning. | Large residency and a model-specific integration surface; no normal production role remains. |

There is no mandatory large-reasoner round. Run GPT-OSS-120B once only when a
Harmony/MXFP4 comparison has been explicitly predeclared, and never make it
resident merely because its weights nominally fit.

## 6. Speech to text

The right result may be two routes: a fast home-command engine and a more
accurate meeting engine.

| Tier | Candidate | Pros | Cons and risks |
| --- | --- | --- | --- |
| **Must-run; Danish meeting-quality candidate** | `CoRal-project/roest-v3-whisper-1.5b` | Danish-specific Whisper fine-tune; 13.92% mean WER and 21.08% CoRal conversation WER in the common Danish benchmark, materially ahead of stock Whisper. | Custom OpenRAIL-derived terms; no maintained Wyoming service; Plaud, timestamp, code-switch, ARM64, and GB10 tests remain. [model card](https://huggingface.co/CoRal-project/roest-v3-whisper-1.5b), [common benchmark](https://huggingface.co/datasets/RyeAI/danish-asr-leaderboard) |
| **Conditional Danish conversation challenger** | `syvai/hviske-v5.3` | Lowest CoRal conversation WER (19.68%) among the selected local candidates; documented Transformers and OpenAI-compatible vLLM paths. | CC-BY-NC-4.0 blocks an assumed work-meeting deployment; custom code and no maintained Home Assistant adapter. [model card](https://huggingface.co/syvai/hviske-v5.3) |
| **Must-run; operational integration control** | `openai/whisper-large-v3-turbo` | Multilingual transcription and timestamps; maintained Home Assistant/Wyoming path. | Common Danish benchmark reports 28.59% mean WER and 63.83% conversation WER; it is not the Danish accuracy ceiling. [OpenAI card](https://huggingface.co/openai/whisper-large-v3-turbo), [Home Assistant Whisper app](https://github.com/home-assistant/addons/tree/master/whisper) |
| **Conditional home-latency candidates** | `syvai/hviske-v5-tiny` and `nvidia/parakeet-rnnt-110m-da-dk` | Both are small, Danish-specific candidates; Hviske supplies several local runtime formats and Parakeet declares Blackwell compatibility. | Both need an owned adapter. Hviske is gated/noncommercial with no timestamps or streaming; Danish Parakeet is not yet supported by Riva and is weak on the common conversational benchmark. [Hviske](https://huggingface.co/syvai/hviske-v5-tiny), [Parakeet](https://huggingface.co/nvidia/parakeet-rnnt-110m-da-dk) |
| **Multilingual controls** | `openai/whisper-large-v3` and `nvidia/parakeet-tdt-0.6b-v3` | Established Whisper tooling; Parakeet adds Danish/English, punctuation, timestamps, and long-audio modes. | Neither leads the common Danish benchmark. Keep them to measure integration, mixed-language behavior, timestamps, and throughput. |
| **Not a current Danish candidate** | Home Assistant Speech-to-Phrase | Closed, predictable supported-command path and efficient local routing before an open-ended model. | The official current language list does not include Danish, and the engine only recognizes known Home Assistant phrases rather than general speech. Reconsider it only if Danish is officially added. [Speech-to-Phrase](https://github.com/OHF-Voice/speech-to-phrase) |

**Why the recommendation is split.** Whisper Turbo remains the strongest
day-one operational path because the maintained Home Assistant app and Wyoming
route already exist. The common Danish benchmark makes Røst the first meeting
accuracy candidate and justifies small-model latency tests for home commands.
Multilingual controls remain necessary for code-switching and timestamps.

**Exact switch rule.** Every general STT winner must integrate through the
required Home Assistant and Meeting Assistant boundaries, preserve Danish/
English without forced translation, keep clean Danish WER at or below 15%,
noisy/far-field WER at or below 25%, and p95 RTF at or below 0.5. Among passing
candidates, choose the lowest p95 first-partial/final latency for `home` and the
lowest WER/speaker-alignment error for `meeting`. Parakeet cannot be promoted
until its maintained adapter passes those actual endpoint tests. A single model
receives both routes only if it wins both rules and the mixed-load test;
otherwise keep the split. Do not place Speech-to-Phrase in the Danish route
until its official language support changes.

## 7. Danish text to speech

| Tier | Candidate | Pros | Cons and risks |
| --- | --- | --- | --- |
| **Must-run; operational recommendation** | Piper `da_DK-talesyntese-medium` | Maintained local engine; exact Danish voice in Wyoming/Home Assistant inventory; small ONNX artifact; CPU deployment can preserve GB10 capacity; phoneme overrides can stabilize names. | No published Danish pronunciation score or MOS; the voice was fine-tuned from an English voice; naturalness may trail Røst. [Piper engine](https://github.com/OHF-Voice/piper1-gpl), [voice card](https://huggingface.co/rhasspy/piper-voices/blob/main/da/da_DK/talesyntese/medium/MODEL_CARD), [Home Assistant Piper](https://www.home-assistant.io/integrations/piper/) |
| **Must-run; preferred promotion candidate** | `CoRal-project/roest-v3-chatterbox-350m` | Fine-tuned on more than 2,000 hours of Danish; publisher MOS 4.01 from 20 native Danish listeners; smaller/faster Chatterbox-Turbo basis. | No published pronunciation or local TTFA/RTF result; stochastic output; long text needs splitting; archived custom inference fork; no maintained Wyoming service; exact weight license needs confirmation. [Røst 350M card](https://huggingface.co/CoRal-project/roest-v3-chatterbox-350m) |
| **Must-run quality ceiling** | `CoRal-project/roest-v3-chatterbox-500m` | Publisher MOS 4.23 and the same Danish dataset/voices; tests whether more capacity gives a meaningful listening win. | Larger, no published TTFA/RTF, sentence splitting/custom adapter, same license/runtime uncertainty; the 0.22 MOS publisher gap has no reported confidence interval. [Røst 500M card](https://huggingface.co/CoRal-project/roest-v3-chatterbox-500m) |

**Why Piper starts.** It is the only candidate here with the maintained direct
Home Assistant/Wyoming route and can remain available independently of GPU
model failures. That operational advantage is worth more than an unverified
naturalness claim on day one.

**Exact switch rule.** A candidate must achieve at least 95% pronunciation pass,
mean naturalness at least 3.5/5, p95 first audio at most 750 ms, p95 RTF at most
0.5, and reliable local Home Assistant/Wyoming or approved adapter operation.
Promote Røst 350M only if it passes all gates, its exact license is accepted,
and its blinded native-Danish naturalness score exceeds Piper's. Consider 500M
only if 350M fails the listening target or does not beat Piper; promote 500M only
if it then passes every latency/integration/license gate and has the best blind
listening score. Otherwise keep Piper and use safe phoneme aliases.

## 8. Diarization

| Tier | Candidate | Pros | Cons and risks |
| --- | --- | --- | --- |
| **Must-run; recommended recorded-meeting candidate** | `pyannote/speaker-diarization-community-1` | Local/offline after gated download; CC-BY-4.0; CPU or CUDA; ordinary plus exclusive diarization; known/min/max speaker controls; exclusive output helps align turns to ASR timestamps. | Gated initial download/token and terms; no first-party DGX Spark recipe; publisher benchmarks are not Danish Plaud meetings; overlap, ARM64/CUDA packaging, memory, and word-to-speaker merge remain local gates. [model card](https://huggingface.co/pyannote/speaker-diarization-community-1), [pyannote.audio](https://github.com/pyannote/pyannote-audio) |
| **Conditional live/streaming challenger** | `nvidia/diar_streaming_sortformer_4spk-v2.1` | NVIDIA 117M online streaming model with NeMo/CUDA paths and short/long buffering profiles; answers a distinct future live-meeting latency question. | Maximum four speaker outputs and documented degradation beyond four; trained primarily in English and may degrade on non-English audio; no Danish or exact DGX Spark/FP4 evidence. It is not needed for the current offline Plaud path. [NVIDIA card](https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2.1) |
| **Rejected production alternative** | Hosted pyannote `precision-2` | Publisher reports lower DER than Community-1 on its benchmark table. | Runs on pyannoteAI servers, violating the repository's local-only meeting default. It can be a separately approved non-sensitive reference, not fallback. |

**Why Community-1 is recommended.** It satisfies the local/offline boundary,
matches the current recorded-meeting workload, and exposes the features needed
for ASR alignment. Sortformer becomes relevant only if live streaming with four
or fewer speakers enters scope. Adding older `speaker-diarization-3.1` would be
a regression control, not a credible first production challenger, because
pyannote's own current table generally favors Community-1.

**Exact switch rule.** Community-1 must pass the pinned two-/three-speaker,
overlap, noisy-room, and code-switched corpus; anonymous speaker labels,
word-to-speaker attribution, local-only execution, and mixed-load recovery are
hard gates. The repository currently specifies what to measure but no maximum
DER or attribution-error threshold. Therefore no alternative can be selected
auditably yet: freeze those two numerical thresholds before Phase D. If
Community-1 misses either, compare Sortformer only for a four-or-fewer-speaker
streaming workload; for recorded or larger meetings, research another **local**
candidate. Do not switch silently to hosted Precision-2.

## 9. Embeddings and reranking

This service remains deferred until a real owner-scoped private corpus and
retrieval contract exist.

| Tier | Candidate | Pros | Cons and risks |
| --- | --- | --- | --- |
| **Conditional first pair; recommended** | `Qwen/Qwen3-Embedding-0.6B` + `Qwen/Qwen3-Reranker-0.6B` | 0.6B each; 32K input; 100+ languages; instruction-aware; embedding supports 32–1024 output dimensions; small enough for a modest resident service. | Publisher multilingual aggregate scores do not establish Danish household/work retrieval; two services and instruction/version discipline are still required; no need is proven without a corpus. [embedding card](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B), [reranker card](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B) |
| **Conditional quality step** | Qwen3 Embedding 4B + Reranker 4B | Same official family and 32K context; Qwen's aggregate multilingual table reports clear gains over 0.6B and makes 4B the justified upgrade point. | About 6.7 times the parameters per model; more latency/memory; publisher aggregate scores cannot justify the cost on this Danish/private corpus. [Qwen family release](https://qwenlm.github.io/blog/qwen3-embedding/) |
| **Optional ceiling** | Qwen3 Embedding 8B + Reranker 8B | Highest-capacity official family control. | Candidate explosion and residency cost; Qwen's published aggregate results show diminishing and non-uniform gains over 4B, including reranking metrics where 4B is better. Run only if 4B still misses a predeclared corpus gate. |

**Why 0.6B starts.** Retrieval runs frequently and below the answer model; the
small pair has the same published 32K and multilingual feature set as the larger
family. Corpus recall and isolation, not model size, are the decision.

**Exact switch rule.** Do not install any pair until there is a versioned corpus
with principal/data-domain/visibility scope, relevance judgments, Danish/English/code queries, and
cross-profile denial cases. The selected pair must have zero cross-scope result
leakage and meet pre-registered recall@k, nDCG@k, p95 latency, and memory limits.
Start at 0.6B. Select 4B only if 0.6B misses a quality minimum and 4B meets all
quality and operational limits; select 8B only under the same rule after 4B
fails. The repository currently has no numerical retrieval minima, so these
must be added before the deferred service enters scope.

## Documentation changes this analysis implies

The model list itself does not need further expansion. The documentation does
need to preserve alternatives without implying that they are all mandatory:

1. Put **must-run**, **conditional**, and **optional control** beside candidates
   in the canonical installation and role-evaluation pages.
2. Add the exact baseline-to-alternative triggers above, especially the
   Home Assistant latency trigger and Coder-Next-versus-Nemotron coding rule.
3. Add explicit `research`, meeting text, and 64K `assistant` quality rubrics;
   `automation` currently has the clearest numerical text gate.
4. Add numerical diarization DER/attribution and retrieval recall/nDCG gates
   before those services can claim a winner.
5. Preserve exact artifact/runtime tuples. Coder-Next FP8, gpt-oss MXFP4,
   NVIDIA NVFP4, MTP, and DSpark are different test variables; none should
   inherit another model's launch flags.
6. For every promoted NVIDIA artifact, record the actual GB10 compute path.
   Do not describe NVFP4 storage as native FP4 acceleration unless startup and
   kernel evidence establish W4A4 execution.

This matrix remains the cross-role synthesis of why each provisional
recommendation leads, what the alternatives are for, and which evidence would
change the decision. The role-specific pages remain authoritative for their
fixtures and acceptance procedures.
