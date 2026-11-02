# Popular TTS models compared with Plapre: Danish and English

Verified: 2026-11-02. Source research only; no new audio benchmark, host
inspection, model download, deployment, or routing change was performed.

## Recommendation

Keep the pinned Plapre Nano v2 as the Danish voice baseline while testing
alternatives. With the owner's clarified priority of Danish first and English
second, **OmniVoice** is the first audition recommendation for noncommercial
household use; **Chatterbox Multilingual V3** is the alternative when permissive
commercial licensing matters. This refines the initial Chatterbox-first
operational shortlist using the Danish evidence below. **VoxCPM2** remains
another bilingual candidate. For responsive speech
without competing for Spark's GPU, compare **Supertonic 3** with the already
declared **MOSS-TTS-Nano** and its Piper fallback. For a separate English voice,
**Kokoro-82M** is the first candidate by download adoption and small size. These are evaluation
priorities inferred from language support, runtime demands, and licensing;
they are not measured quality rankings.

Plapre's publisher describes a Danish model, not an English model. Its 335M
backbone supports voice references, pronunciation references, pace control,
conversational context, and editing. Its weights are CC BY 4.0. A multilingual
model offers an English path and potentially consistent speaker identity across
languages, but its Danish pronunciation still needs direct comparison with
Plapre. [Plapre model card](https://huggingface.co/syvai/plapre-nano-v2),
[Plapre source](https://github.com/syv-ai/plapre).

## Danish-first comparison: OmniVoice versus Chatterbox

Both publishers explicitly support Danish and English. OmniVoice's paper
reports 1,665.98 hours of Danish training audio and 1.79% Danish character error
rate on its FLEURS-Multilingual-102 evaluation, measured through an ASR model.
This is concrete Danish intelligibility evidence, not a native-listener
naturalness score. Its Danish result does not compare against Chatterbox V3
or Plapre. No equivalent Danish-specific V3 benchmark was established in this
review. The additional evidence makes OmniVoice a stronger first audition for
this Danish-first household requirement, but does not establish that it sounds
better. [OmniVoice paper, Appendix C and D](https://arxiv.org/html/2604.00688v1),
[Chatterbox language support](https://huggingface.co/ResembleAI/chatterbox).

For English, both are supported candidates; no directly comparable evaluation
of current Chatterbox V3 against OmniVoice was established here. For deployment,
Chatterbox's MIT license is more flexible than OmniVoice's noncommercial
pretrained-weight terms. Neither candidate has verified Danish first-audio
latency on this GB10. The recommendation therefore remains conditional on
native Danish listening and target-hardware measurements, with English as a
secondary acceptance set. [Chatterbox model card](https://huggingface.co/ResembleAI/chatterbox),
[OmniVoice model license](https://huggingface.co/k2-fsa/OmniVoice#license).

For either model, use an approved clean native Danish reference for Danish
output. Both publishers warn that cross-language references can transfer the
reference speaker's accent. When consistent identity matters in both languages,
prefer Danish and English recordings from the same bilingual speaker rather
than assuming a Danish-only reference will produce native English.
[Chatterbox reference guidance](https://huggingface.co/ResembleAI/chatterbox#tips),
[OmniVoice reference guidance](https://github.com/k2-fsa/OmniVoice#voice-cloning).

## What “popular” means here

The table uses Hugging Face's **downloads last month** as an observable
adoption proxy, queried through each model's public API on the verification
date. Counts are rounded and can change or differ from cached listing pages.
They are repository-level download events, not unique users, production
installations, quality scores, or commercial market share. HEAD requests and
library-specific query files affect counting. A repository can contain several
checkpoints; conversions and mirrors are not added to the base model total.
[Counting methodology](https://huggingface.co/docs/hub/models-download-stats),
[download-sorted TTS listing](https://huggingface.co/models?pipeline_tag=text-to-speech&sort=downloads).

The shortlist focuses on widely downloaded base models useful for English or
Danish. It excludes collections such as `audio-cpp/audio.cpp-gguf`, duplicate
quantizations, and models focused on other languages. Danish-specific and CPU
candidates are included even when less downloaded.

## Local models

“Yes” means publisher-declared language support. It does not establish Danish
quality, fluent Danish/English switching inside one sentence, or local latency.
Sizes below are publisher headline sizes, not complete runtime memory costs.

| Model / checkpoint family | Downloads/month, approximately | Danish | English | Comparison with Plapre |
| --- | ---: | --- | --- | --- |
| [Kokoro-82M](https://huggingface.co/hexgrad/Kokoro-82M) | 11.37M | No standard Danish pipeline | Yes, US/UK | 82M; attractive small English companion; Apache-2.0 weights. Does not replace the Danish route. [Supported pipeline languages](https://github.com/hexgrad/kokoro/blob/main/kokoro/pipeline.py). |
| [XTTS-v2](https://huggingface.co/coqui/XTTS-v2) | 5.97M | No, outside its 17 languages | Yes | Established voice cloning from short references; custom Coqui Public Model License. Its popularity does not make it a Danish alternative. |
| [OmniVoice](https://huggingface.co/k2-fsa/OmniVoice) | 2.93M | Yes | Yes | About 0.6B; 600+ languages, cloning and voice design. Broad multilingual challenger, but code is Apache-2.0 while pretrained weights are **CC-BY-NC**. [Explicit Danish/English inventory](https://github.com/k2-fsa/OmniVoice/blob/master/docs/languages.md). |
| [Chatterbox family](https://huggingface.co/ResembleAI/chatterbox) | 2.20M shared repository | Yes, **Multilingual** | Yes | Multilingual is 500M with 23 languages and cloning; MIT. Strong bilingual candidate with flexible licensing. Original Chatterbox, Turbo 350M, and Nano 110M are English models: do not transfer multilingual support to them. [Variant/language table](https://github.com/resemble-ai/chatterbox). |
| [Qwen3-TTS 1.7B CustomVoice](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-1.7B-CustomVoice) | 1.72M | No, outside its 10 languages | Yes | Apache-2.0; preset voices and instruction control. A capable English challenger, not a Danish replacement. CustomVoice is distinct from Base voice cloning and VoiceDesign. The smaller [0.6B CustomVoice](https://huggingface.co/Qwen/Qwen3-TTS-12Hz-0.6B-CustomVoice) has about 766K downloads. |
| [F5-TTS](https://huggingface.co/SWivid/F5-TTS) | 1.26M | Not in the official base checkpoint's declared coverage | Yes | Official base weights cover Chinese/English and are CC-BY-NC-4.0; code MIT. Community language fine-tunes need separate evaluation. [Publisher checkpoint inventory](https://github.com/SWivid/F5-TTS/blob/main/src/f5_tts/infer/SHARED.md). |
| [VoxCPM2](https://huggingface.co/openbmb/VoxCPM2) | 969K | Yes | Yes | About 2B; 30 languages, 48kHz output, streaming, voice design/cloning; Apache-2.0. Worth comparing for expressive bilingual speech; larger than Plapre and not GB10-qualified here. |
| [MOSS-TTS Local Transformer](https://huggingface.co/OpenMOSS-Team/MOSS-TTS-Local-Transformer) | 151K | Yes | Yes | Publisher calls it Local-1.7B; the Hub's file summary reports 3B. Twenty languages, cloning, duration/pronunciation controls; Apache-2.0. A larger runtime and integration effort than Plapre. Distinct from the separate 8B MOSS-TTS checkpoint. |
| [Supertonic 3](https://huggingface.co/Supertone/supertonic-3) | 85K | Yes | Yes | About 99M across public ONNX assets; 31 languages and CPU inference. Strong CPU challenger to Piper and Plapre's latency path. Model OpenRAIL-M; sample code MIT. Public package supplies preset styles; creating custom reference styles uses the publisher's Voice Builder service. |
| [Fish Audio S2 Pro](https://huggingface.co/fishaudio/s2-pro) | 48K | Yes, lower-priority supported tier | Yes, tier 1 | Roughly 4B + 400M; 80+ languages and expressive streaming. Research/noncommercial weight license; commercial use requires separate licensing. Not an unrestricted local replacement. |
| [Fish Audio Drama 3](https://huggingface.co/fishaudio/drama-3) | 35K | Listed in model metadata | Yes | 35B total / 3B active MoE; multi-character and long-form emphasis. Much larger footprint than Plapre. Its [Community License](https://huggingface.co/fishaudio/drama-3/blob/main/LICENSE) differs from S2 Pro's and includes revenue and hosted-service conditions. |
| [Plapre Nano v2](https://huggingface.co/syvai/plapre-nano-v2) | 1.1K | Yes, dedicated Danish | Not publisher-supported | Existing 335M Danish baseline; CC BY 4.0. Low global downloads do not establish worse Danish speech. |
| [Røst v3 350M](https://huggingface.co/CoRal-project/roest-v3-chatterbox-350m) / [500M](https://huggingface.co/CoRal-project/roest-v3-chatterbox-500m) | Too small/inconsistently counted to rank usefully | Yes, dedicated Danish | 500M card warns of a heavy Danish accent; 350M English not established | Danish fine-tunes on 2,000+ hours. Publisher MOS 4.01/4.23 from 20 Danish listeners supports an audition, not superiority to Plapre. Long text needs splitting; generic OpenRAIL metadata needs exact weight terms checked. |
| [MOSS-TTS-Nano 100M ONNX](https://huggingface.co/OpenMOSS-Team/MOSS-TTS-Nano-100M-ONNX) | Not tracked | Yes | Yes | 100M plus a separate audio tokenizer; CPU ONNX, cloning, streaming, 48kHz stereo, Apache-2.0. Already declared in this repository. Distinct from the Local Transformer above. [20-language list](https://github.com/OpenMOSS/MOSS-TTS-Nano#supported-languages). |
| [Piper voices](https://huggingface.co/rhasspy/piper-voices) | Voice collection; not comparable | Yes, separate Danish voice | Yes, separate English voices | Existing CPU fallback and practical Home Assistant integration. No cross-language cloned identity. Current [engine](https://github.com/OHF-Voice/piper1-gpl) is GPL-3.0; inspect each voice's terms separately. The [Danish talesyntese card](https://huggingface.co/rhasspy/piper-voices/blob/main/da/da_DK/talesyntese/medium/MODEL_CARD) specifies a CC0 dataset; the collection is tagged MIT. |

Download figures are from the API endpoint `https://huggingface.co/api/models/`
followed by the exact linked repository ID. For example,
[Kokoro's API record](https://huggingface.co/api/models/hexgrad/Kokoro-82M).
The Chatterbox number covers a shared repository and cannot be attributed to
Multilingual V3 alone.

Chatterbox's current README advertises Multilingual V3 while its inspected
loader still names `t3_mtl23ls_v2.safetensors` as the default multilingual
checkpoint. Explicitly select `t3_model="v3"` when evaluating V3, then pin the
actual checkpoint and loader before benchmarking;
a family name or README announcement is insufficient version evidence.
[Loader source](https://github.com/resemble-ai/chatterbox/blob/master/src/chatterbox/mtl_tts.py).

Røst's archived inference fork and its licensing details are discussed in the
[earlier Danish comparison](danish-tts-recommendation.md). The fork remains
archived in the [publisher repository](https://github.com/alexandrainst/coral_chatterbox).
Its Danish MOS evaluation did not include Plapre, so those scores cannot be
used to calculate a quality gap against Plapre.

## What the existing HomeCompute evidence says

The [2026-10-28 operational record](../current-state.md) reports Plapre reply
synthesis of 4.37 seconds in one Home Assistant chain, direct first audio around
5.5 seconds, and Piper synthesis of the same reply in 0.94 seconds. The upstream
Plapre path generates/vocodes an entire sentence before returning PCM; smaller
proxy chunks do not remove that delay. The owner chose to retain Plapre as the
preferred voice. These are dated point observations, not current live checks,
an apples-to-apples streaming benchmark, or p95 results.

The current [home-core speech declaration](../../deploy/piper-tts/README.md)
defines MOSS-TTS-Nano ONNX with the `Adam` reference at the CPU Wyoming
endpoint, with Piper as its internal fallback. This is source configuration,
not a fresh live verification. Include Nano in any CPU comparison rather than
assuming the older Piper timing describes the current primary CPU voice.

The repository pins Plapre Nano v2 revision
`007e0b471e377dd5061786f7df6ad659d15c4d5f`; the direct model API returned that
same SHA. Upstream tree commit text mentions Nano v3, but that naming detail
alone establishes no newer installed or downloadable revision. Keep the exact
pin as the comparison identity; this research establishes no version drift.
[Pinned deployment analysis](plapre-nano-v2-gb10-home-assistant-deployment-2026-09-26.md),
[upstream tree](https://huggingface.co/syvai/plapre-nano-v2/tree/main),
[direct API](https://huggingface.co/api/models/syvai/plapre-nano-v2).

## Hardware, latency, and the comparison needed

Spark is ARM64 GB10, so generic CUDA support is insufficient evidence of a
working deployment: PyTorch, attention kernels, codec dependencies, image
architecture, and the selected model must all work together. Keep each
candidate isolated and pinned. Plapre already has a repository-owned ARM64
[deployment tuple](plapre-nano-v2-gb10-home-assistant-deployment-2026-09-26.md);
the other GPU candidates have not been validated on this host in this review.
Model backbone parameters alone do not include codecs, vocoders, KV cache,
activations, or contention with resident text models.

Publisher speed claims use different hardware and definitions. For example,
VoxCPM2 cites RTX 4090 RTF around 0.3, while S2 Pro cites roughly 100ms first
audio on H200. OmniVoice advertises RTF as low as 0.025. These figures cannot
predict Danish first audio on GB10, nor be ranked against Home Assistant's
end-to-end response time. [VoxCPM2](https://huggingface.co/openbmb/VoxCPM2),
[S2 Pro](https://huggingface.co/fishaudio/s2-pro),
[OmniVoice](https://huggingface.co/k2-fsa/OmniVoice).

Use the existing [Danish qualification suite](../tts-qualification.md) and add
an equivalent English set. Include short confirmations, longer replies, Danish
names and addresses, dates, decimals, abbreviations, English product names in
Danish, and deliberate Danish/English switching. Blind native listeners should
score pronunciation and naturalness separately. Repeat stochastic generations;
record warm/cold first audio, p50/p95, RTF, failure rate, memory, and results
under concurrent LLM load. Measure both direct runtime and the authenticated
Home Assistant/OpenClaw client path.

The existing Danish gates are p95 first audio at most 750ms, p95 RTF at most
0.5, pronunciation pass at least 95%, and naturalness at least 3.5/5. Preserve
ASR verification, bounded resynthesis, and Piper fallback when testing a
candidate. A voice that sounds good but silently changes names or numbers has
not passed. [Qualification configuration](../../config/tts-qualification.json).

## Speed and resource evidence

The following compares **direct Chatterbox Multilingual V3** with **direct
OmniVoice**, not Chatterbox Turbo or an optimized NVIDIA NIM deployment.
No matched Danish/English GB10 benchmark or measured peak-memory comparison
was found. RTF is synthesis time divided by generated audio duration; 0.1
means ten seconds of speech generated in one second. It is not first-audio
latency, and batched throughput is not one user's response time.

| Measure | Chatterbox Multilingual V3 | OmniVoice |
| --- | --- | --- |
| Main speech model | Publisher calls it 500M; additional voice encoder, S3Gen/vocoder and watermarking are used. | About 613M parameters in the main checkpoint; a separate Higgs audio tokenizer is also loaded. |
| Selected original weight files on disk | **3.207GB / 2.987GiB**: V3 T3 + `s3gen.pt` + `ve.pt` + built-in conditioning. Not the whole shared repository. | **3.256GB / 3.032GiB**: main checkpoint + bundled audio tokenizer. |
| Published direct-runtime speed | No V3-specific hardware/RTF/precision benchmark established in the reviewed publisher sources. Turbo and the hosted Resemble service's speed claims do not apply to V3. | Paper: H20, PyTorch, 3s reference and 10s output, batch 1: RTF **0.0319 at 16 steps**, **0.0598 at 32 steps**. This speed subsection does not specify precision. |
| Newer optimized-runtime result | No equivalent first-party direct V3 result established. NVIDIA NIM results describe its separately optimized serving stack. | Official README: H100, FP16, 32 steps, 2,020 Chinese Seed-TTS samples / 3.3h: batch 1 RTF **0.0899 baseline**, **0.0430 FlashInfer**, **0.0367 FlashInfer + CUDA graphs**. Batch 8 FlashInfer RTF **0.0115** is aggregate throughput. |
| Actual RAM / GPU memory | No publisher V3 peak RAM/VRAM requirement or GB10 measurement established; independent measurements below are hardware-specific. | No authoritative peak RAM/VRAM table established. Main weights, codec, activations, guidance, precision, reference length and batching all contribute. Optional ASR adds another model. |
| Direct Python first audio | `generate()` completes speech-token generation, waveform synthesis and watermarking before returning a tensor. | `generate()` returns completed waveforms. Long-text chunking collects chunks before final decoding/return; it does not yield playable chunks to the caller. |

Speed sources: [OmniVoice paper §4.3.1/Table 8](https://arxiv.org/html/2604.00688v1#S4.SS3.SSS1),
[official FlashInfer table](https://github.com/k2-fsa/OmniVoice#flashinfer-acceleration).
Weight sizes are sums from pinned file metadata, excluding small tokenizer/config
files and package assets: [Chatterbox API at `5bb1f6e`](https://huggingface.co/api/models/ResembleAI/chatterbox/revision/5bb1f6ee58e50c3b8d408bc82a6d3740c2db6e18?blobs=true),
[OmniVoice API at `c5fdb5c`](https://huggingface.co/api/models/k2-fsa/OmniVoice/revision/c5fdb5ccb189668d56333f77ba2629f4cd7535f4?blobs=true).
Architecture/API sources: [Chatterbox multilingual loader and generation](https://github.com/resemble-ai/chatterbox/blob/master/src/chatterbox/mtl_tts.py),
[OmniVoice loader, generation and chunking](https://github.com/k2-fsa/OmniVoice/blob/master/omnivoice/models/omnivoice.py).

For the paper's fixed ten-second output, the reported single-item RTFs imply
about **319ms** or **598ms** of synthesis respectively. Those are arithmetic
conversions of the published H20 result, not measured Danish first audio,
service overhead, or GB10 predictions. The H20 and H100 rows use different
workloads; their numbers must not be treated as a GPU speed comparison.

An independent reproducible V3 report tested upstream commit `5de7a54`, Windows,
RTX 5080 16GB and PyTorch 2.7.1/cu128, FP32 on 2026-10-23. English results were
**2.03s synthesis / 5.1s audio** without reference, **2.65s / 6.7s** with cloning
(RTF about 0.40). Warm values average runs 2–3. It reports **3.2–3.5GB PyTorch
allocated GPU memory**, **5.3–5.6GB whole-GPU usage including a roughly 1.6GB
desktop**, and process RAM roughly **2.5GB after load / 3.0GB after long input**.
These are the author's measurements, not publisher results or a Danish/GB10
capacity guarantee. [Original benchmark and downloadable scripts](https://vram16.ullu-egg.com/en/models/chatterbox-multilingual/).

NVIDIA's separate Chatterbox NIM has a supported **FP16 batch-8 DGX Spark
profile**, listed at **44.61GiB GPU memory and 4.86GiB host memory**. Its Spark
benchmark at **eight simultaneous streams** reports **663.48ms average /
1,321.71ms p95 first audio**, **10.63 aggregate RTFX**, on English LJSpeech
inputs. This proves a documented Spark serving option, not direct V3's resource
requirement, single-user latency, or Danish performance. The page does not
identify its checkpoint as V3. [NIM support/memory table](https://docs.nvidia.com/nim/speech/latest/reference/support-matrix/tts.html#chatterbox-tts-multilingual),
[NIM benchmark methodology/results](https://docs.nvidia.com/nim/speech/latest/reference/performances/tts/performance.html).

OmniVoice defaults to 32 iterative steps; 16 is an explicit speed/quality
choice. Its FlashInfer path recommends CUDA graphs for batch 1. Neither the
H100 result nor generic CUDA support establishes that its particular kernels
work on ARM64 GB10. [Generation settings](https://github.com/k2-fsa/OmniVoice/blob/master/docs/generation-parameters.md),
[FlashInfer implementation](https://github.com/k2-fsa/OmniVoice/blob/master/omnivoice/models/omnivoice_flashinfer.py).

OmniVoice's loader passes the selected precision to the main model but loads
the separate codec without that same explicit dtype argument. Supplying a
reference transcript avoids lazy-loading Whisper Large v3 Turbo for automatic
transcription. Cache approved voice conditioning in both models before warm
latency tests. Disk checkpoint sizes and parameter-bytes calculations are
storage or weight-only lower bounds, not measured process RAM or reserved GPU
memory. On GB10, GPU allocations also share physical memory with CPU processes
and resident inference services. Until both exact runtimes are measured,
**OmniVoice has the stronger documented speed evidence; which model is faster
or smaller on this GB10 remains unverified**.
[OmniVoice loader/reference preparation](https://github.com/k2-fsa/OmniVoice/blob/master/omnivoice/models/omnivoice.py),
[Chatterbox cached conditionals](https://github.com/resemble-ai/chatterbox/blob/master/src/chatterbox/mtl_tts.py).

## Optional cloud reference points

These are prominent hosted alternatives with declared Danish and English
support, not a verified market-share ranking. They send synthesis inputs to an
external provider and are not implicit HomeCompute fallback routes.

| Service/model | Relevant comparison |
| --- | --- |
| [ElevenLabs Eleven v4 / v4 Turbo](https://elevenlabs.io/docs/overview/models) | Broad multilingual, expressive hosted comparison. Vendor latency figures exclude parts of the network/application path and do not establish Danish quality against Plapre. |
| [Cartesia Sonic 3.6](https://docs.cartesia.ai/build-with-cartesia/tts-models/latest) | Hosted low-latency challenger; 44 languages. [Danish and English language inventory](https://www.cartesia.ai/languages). |
| [Azure Neural TTS](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/language-support?tabs=tts) | Useful conventional reference voices: `da-DK-ChristelNeural`, `da-DK-JeppeNeural`, and extensive English voice choices. |

No common Danish listening test against Plapre was established in this review.
For the requested local comparison, the next useful evidence is a controlled
audition of Plapre, OmniVoice, Chatterbox Multilingual, VoxCPM2, Supertonic 3, the declared
MOSS-TTS-Nano, and Piper, with Kokoro added for English. Røst is a separate Danish specialist audition;
OmniVoice is the first Danish-first audition recommendation when its
noncommercial weight terms fit the intended use.
