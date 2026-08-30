# Model-role trade-off matrix

Verified: 2026-08-30

Status: decision aid from repository requirements and first-party model/runtime
documentation. It does not select a production artifact or authorize a
deployment change.

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
| Shared Danish `automation`, `research`, and `meeting` text work | `nvidia/Qwen3.6-35B-A3B-NVFP4` | Baseline is clear; dense quality challengers still need local Danish and workflow tests |
| Home Assistant `home` | Shared Qwen3.6 first | Genuinely open if mixed-load voice latency fails; then a reserved small model may win |
| Codex `coding` | `Qwen/Qwen3-Coder-Next-FP8` | Genuinely open against Nemotron 3.5 Lightning/DSpark and dense controls |
| Hermes `assistant` | Qwen3.6 in a separate 64K-or-greater qualification profile | Baseline path is clear; acceptance at 64K and mixed load is not |
| Serialized high-capacity reasoning | `openai/gpt-oss-120b` in publisher-native MXFP4 first | Open against Nemotron 3 Super; neither has Danish acceptance evidence |
| Home STT | Whisper large-v3-turbo operationally; Danish Parakeet as the likely promotion candidate after an adapter exists | Open on real far-field, code-switched speech, and end-to-end integration |
| Meeting STT | Whisper large-v3-turbo first, large-v3 as the accuracy ceiling | Genuinely open; the home and meeting winners may differ |
| Danish TTS | Piper operational baseline; Røst 350M promotion candidate | Genuinely open on pronunciation, naturalness, integration, and latency |
| Diarization | `pyannote/speaker-diarization-community-1` for recorded meetings | NVIDIA Streaming Sortformer is a distinct conditional live/low-latency challenger; numerical acceptance thresholds are still missing |
| Embeddings/reranking | Qwen3 0.6B pair, deferred until a real corpus exists | Open against 4B only if the small pair misses a pre-registered retrieval gate |

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
| **Must-run dense challenger** | `nvidia/Gemma-4-31B-IT-NVFP4` | Dense quality control; 256K context, multimodal input, function calling, Apache-2.0; NVIDIA-published ModelOpt artifact listed in the Spark vLLM matrix. | Dense 31B may cost more active compute and mixed-load headroom than a 3B-active MoE. The generic card does not prove the exact GB10 kernel path or Danish acceptance. [NVIDIA card](https://huggingface.co/nvidia/Gemma-4-31B-IT-NVFP4), [Spark matrix](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/vllm/README.md#model-support-matrix) |
| **Must-run dense challenger** | `Qwen/Qwen3.8-27B-FP8` | Newer dense Qwen quality hypothesis; official publisher FP8 checkpoint; 262K context and reasoning/tool features. | No Qwen/NVIDIA-published NVFP4 checkpoint or exact one-Spark recipe was found. Dense FP8 residency and the required recent runtime may cost more operationally; Danish task results remain local work. [Qwen card](https://huggingface.co/Qwen/Qwen3.8-27B-FP8) |
| **Conditional English-agent challenger** | `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` | Exact one-Spark target plus DSpark recipe; 30B/3B active; configurable reasoning and strong first-party agent/coding focus. | Supported post-training list omits Danish; OpenMDW-1.1 needs acceptance; on GB10 it is explicitly W4A16/Marlin rather than native FP4. Do not promote it for Danish Aula, household, or private-note work from English agent benchmarks. [NVIDIA card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4) |
| **Conditional difficult-job control** | `openai/gpt-oss-120b` | 117B total/5.1B active; tools, structured outputs, adjustable reasoning; OpenAI says native MXFP4 fits one 80 GB accelerator and that its released evaluations used that format. | Harmony formatting and exact gateway/tool behavior need qualification; large weight residency/load time reduces mixed-workload flexibility; no cited publisher Danish result. Benchmark native MXFP4 before any local NVFP4 cast. [OpenAI card](https://huggingface.co/openai/gpt-oss-120b) |
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
| **Optional protocol/tool control** | `openai/gpt-oss-20b` | Native MXFP4; OpenAI says it runs within 16 GB; tools and structured outputs provide a compact non-Qwen comparison. | Harmony integration is extra complexity; total/active size does not guarantee 750 ms TTFT; no Danish HA evidence. [OpenAI card](https://huggingface.co/openai/gpt-oss-20b) |
| **Optional controls already in the repository shortlist** | `zai-org/GLM-4.7-Flash`; `mistralai/Devstral-Small-2-24B-Instruct-2512` | GLM is a compact MoE tool-use hypothesis; Devstral is a useful low-memory/latency operational control with publisher-supported agentic repository work. | Neither has a first-party exact Spark runtime path in the current repository evidence; Devstral is coding-specialized; both add benchmark cost without stronger Danish evidence. [GLM card](https://huggingface.co/zai-org/GLM-4.7-Flash), [Devstral card](https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512) |

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
| **Must-run; provisional recommendation** | `Qwen/Qwen3-Coder-Next-FP8` | Purpose-built for coding agents, long-horizon tool use, and recovery; 80B total/3B active; 262K context; Apache-2.0; official publisher FP8 artifact. | Qwen says the card's displayed evaluations used BF16 before FP8 quantization. No first-party Coder-Next NVFP4 or exact Spark recipe was found, so local FP8 quality, memory, and Responses behavior are decisive. [Qwen FP8 card](https://huggingface.co/Qwen/Qwen3-Coder-Next-FP8#model-overview) |
| **Must-run agent/performance challenger** | `nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4` target-only, native MTP, and target+DSpark | Exact GB10 command and 967M DSpark draft; 30B/3B active; NVIDIA reports agent/coding evaluations; DSpark directly tests lower interactive latency. | Three tuples increase test cost. The target uses W4A16/Marlin on GB10; OpenMDW-1.1 needs approval; its supported natural-language list omits Danish. DSpark is speculative decoding, not another model or FP4 format. [target](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4), [draft](https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Lightning-30B-A3B-NVFP4-DSpark) |
| **Must-run dense controls** | `nvidia/Gemma-4-31B-IT-NVFP4`; `Qwen/Qwen3.8-27B-FP8` | Test whether a dense model's quality/reliability beats efficient MoE agent models; both are current publisher artifacts with long context and tool/coding claims. | Higher active compute may hurt interactive latency and mixed load; each needs its own pinned runtime/parser tuple; neither is Codex-qualified by a generic model card. |
| **Must-run resource control** | `mistralai/Devstral-Small-2-24B-Instruct-2512` | 24B, 256K, Apache-2.0, designed for agentic repository work; useful lower-memory/latency and voice-coexistence control. | No first-party GB10 NVFP4 path in current evidence; publisher says it can run on a 32 GB Mac, which does not predict Codex quality or GB10 speed. [Mistral card](https://huggingface.co/mistralai/Devstral-Small-2-24B-Instruct-2512) |
| **Conditional high-capacity control** | `openai/gpt-oss-120b` | Reasoning, tools, and structured output; native MXFP4 nominally fits. | Harmony/Responses compatibility and load/memory headroom may dominate; not coding-specialized. |

**Why Coder-Next remains first.** It is the only first-party artifact in this
set explicitly designed around long-running coding agents and failure recovery.
Nemotron has the strongest exact GB10 performance path, so it is a real
challenger rather than a footnote, but appliance optimization cannot substitute
for implementation correctness and cloud-review acceptance.

**Exact switch rule.** Reject any tuple that fails the Codex Responses/tool loop,
license review, or 10% mixed-load memory headroom. A candidate is promotable only
if at least 70% of the pinned .NET, Python, Vue, and React/TypeScript tasks pass
build/tests and frontier review without cloud reimplementation. Among passing
tuples, use the repository's predeclared weighted coding scorecard; select
Nemotron/DSpark only if its complete-task score exceeds Coder-Next's, or if
Coder-Next fails a hard gate. Select Devstral only if it passes the same 70% gate
and the higher-quality candidates cannot coexist with required P0 voice/memory.
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

## 5. Serialized high-capacity reasoning

| Tier | Candidate | Pros | Cons and risks |
| --- | --- | --- | --- |
| **Must-run; provisional recommendation** | `openai/gpt-oss-120b` in native MXFP4 | 117B/5.1B active; adjustable reasoning, tools, structured output; Apache-2.0; OpenAI states the MXFP4 artifact fits one 80 GB accelerator and that all released evaluations used MXFP4. | Harmony formatting; 120B weight footprint/load time; nominal fit does not prove GB10 mixed-load headroom; no cited Danish workload evidence. An NVFP4 cast is a new artifact. [OpenAI card](https://huggingface.co/openai/gpt-oss-120b) |
| **Must-run NVIDIA control** | `nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4` | NVIDIA-published NVFP4, dedicated one-Spark playbook, tool/reasoning focus. | 12B active versus gpt-oss's 5.1B may cost throughput; custom reasoning/parser/runtime tuple; post-training language list omits Danish; license review and large memory residency. [NVIDIA card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4), [Spark playbook](https://github.com/NVIDIA/dgx-spark-playbooks/blob/main/nvidia/nemotron/README.md) |
| **Optional compact control** | `openai/gpt-oss-20b` | Same publisher-native MXFP4/Harmony family; OpenAI says it runs within 16 GB. | It answers a compact latency/residency question, not the high-capacity ceiling; use it for `home` or bounded agent controls rather than pretending it replaces a 120B comparison. |

**Why gpt-oss-120b goes first.** Its publisher evaluates the released MXFP4
format itself, it has the less restrictive license in this pair, and its lower
active parameter count is a plausible efficiency advantage. Nemotron Super is
the necessary NVIDIA-native control, not an automatic winner because it says
NVFP4.

**Exact switch rule.** Run both only on a pre-labelled difficult corpus after a
shared model winner exists. A 120B candidate must pass its exact parser/tool
contract, privacy and license checks, 10% measured memory headroom in its
approved serialized operating mode, and the relevant Danish suite. Choose
Nemotron only if it passes every hard gate and beats gpt-oss on the predeclared
difficult-task quality score; otherwise retain gpt-oss. Do not make either
resident merely because its weights nominally fit.

## 6. Speech to text

The right result may be two routes: a fast home-command engine and a more
accurate meeting engine.

| Tier | Candidate | Pros | Cons and risks |
| --- | --- | --- | --- |
| **Must-run; Danish promotion candidate** | `nvidia/parakeet-rnnt-110m-da-dk` | Danish-specific 110M FastConformer/RNN-T; NVIDIA reports 8.8–10.7% WER on three Danish evaluation sets; Blackwell compatibility; small enough for a strong latency hypothesis. | Danish only, so mixed Danish/English and meeting punctuation may lose; no stable `/v1/audio/transcriptions` or Wyoming contract is supplied by the model card; the card says it is not yet supported by Riva. Home Assistant's maintained Whisper app supports a different Parakeet identity (`nvidia/parakeet-tdt-0.6b-v3`), not this Danish RNNT, so this model needs a maintained adapter. [NVIDIA card](https://huggingface.co/nvidia/parakeet-rnnt-110m-da-dk), [Home Assistant Whisper app](https://github.com/home-assistant/addons/tree/master/whisper) |
| **Must-run; provisional operational and meeting recommendation** | `openai/whisper-large-v3-turbo` | Multilingual transcription and timestamps; four decoder layers make it much faster than large-v3; Danish is in the declared language set; the maintained Home Assistant app exposes Turbo through Wyoming. | Publisher describes a minor quality loss from pruning large-v3's decoder from 32 to 4 layers; aggregate multilingual support does not prove Danish household or Plaud WER. [OpenAI card](https://huggingface.co/openai/whisper-large-v3-turbo), [Home Assistant Whisper app](https://github.com/home-assistant/addons/tree/master/whisper) |
| **Must-run accuracy ceiling** | `openai/whisper-large-v3` | Full 32-decoder-layer reference for multilingual meeting accuracy and long-form comparison. | Slower and more resource-intensive; may fail p95 RTF/mixed-load gates even if WER is best. [OpenAI card](https://huggingface.co/openai/whisper-large-v3) |
| **Not a current Danish candidate** | Home Assistant Speech-to-Phrase | Closed, predictable supported-command path and efficient local routing before an open-ended model. | The official current language list does not include Danish, and the engine only recognizes known Home Assistant phrases rather than general speech. Reconsider it only if Danish is officially added. [Speech-to-Phrase](https://github.com/OHF-Voice/speech-to-phrase) |

**Why the recommendation is split.** Whisper Turbo is the strongest day-one
operational path because the maintained Home Assistant app and Wyoming route
already exist. Parakeet has the strongest first-party Danish-specific evidence
and size for a later interactive-control promotion, but needs an adapter first.
Whisper Turbo also has the stronger multilingual/open-ended shape for meetings
and code-switching. Large-v3 tests whether Turbo's speed trade-off loses too
much meeting accuracy.

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
with `owner_scope`, relevance judgments, Danish/English/code queries, and
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
