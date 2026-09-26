# Plapre Nano v2 on GB10 and Home Assistant

Verified: 2026-09-26

Scope: primary-source deployment review of `syvai/plapre-nano-v2` on one
NVIDIA GB10 / DGX Spark-class host while the repository's pinned
`unsloth/Qwen3.8-27B-NVFP4` service remains resident. This is not an on-device
qualification result.

## Recommendation

Proceed with a **separate, pinned Plapre service plus a small Wyoming/OpenAI
adapter**, not a second `vllm serve` command and not direct exposure of
Plapre's FastAPI server.

The best official ARM64/GB10 base matching Plapre's declared runtime range is
NVIDIA vLLM 26.02 for Linux/ARM64:

```text
nvcr.io/nvidia/vllm@sha256:604e5b052d1ce1b87952c72bf95cc637192c0051fc15a32886dff20bffd5c514
```

That image contains vLLM 0.15.1, which satisfies Plapre's hard
`vllm>=0.15,<0.16` constraint. NVIDIA publishes the tag as multi-architecture
and identifies the digest above as its ARM64 manifest. Plapre's publisher warns
that newer measured stacks, including vLLM 0.19, produce dramatically more
mid-sentence repetition. The NVIDIA base is therefore the strongest available
official candidate, but it is **not a publisher-qualified complete tuple**:
NVIDIA 26.02 also contains torch 2.11 alpha and Transformers 4.57.5, while
Plapre does not publish an exact torch/Transformers lock. Audition and ASR-check
multi-sentence Danish before promotion. Sources: [Plapre package constraints and
runtime warning](https://github.com/syv-ai/plapre/blob/b111239d3b099cfafcb54b3471a8e9e9ba71ae8b/pyproject.toml),
[pinned Plapre README](https://github.com/syv-ai/plapre/blob/b111239d3b099cfafcb54b3471a8e9e9ba71ae8b/README.md#installation),
[NVIDIA 26.02 component versions and unified-memory warning](https://docs.nvidia.com/deeplearning/frameworks/vllm-release-notes/rel-26-02.html),
[NGC ARM64 manifest digest](https://catalog.ngc.nvidia.com/orgs/nvidia/-/containers/vllm/26.03.post1-py3/tags?_lr=1).

Host prerequisites are an ARM64 GB10 system with a compatible NVIDIA driver,
Docker Engine, and NVIDIA Container Toolkit. No host CUDA Toolkit is required
for the NGC container. Plapre itself requires Python 3.12 or newer and a CUDA
GPU; NVIDIA 26.02 already supplies Python 3.12 and CUDA 13.1.1. The published
ARM64 image is 6.31 GB compressed, before the model artifacts and derived-image
layers. Sources: [NVIDIA container prerequisites](https://catalog.ngc.nvidia.com/orgs/nvidia/-/containers/vllm/26.02-py3?_lr=1),
[Plapre runtime prerequisite](https://github.com/syv-ai/plapre/blob/b111239d3b099cfafcb54b3471a8e9e9ba71ae8b/README.md#installation),
[NVIDIA 26.02 release](https://docs.nvidia.com/deeplearning/frameworks/vllm-release-notes/rel-26-02.html).

## Immutable artifact set

Pin more than the headline checkpoint. The current Plapre loader resolves
several independent artifacts from mutable default branches.

| Artifact | Required pin | License/status | Why it matters |
| --- | --- | --- | --- |
| Plapre model | `syvai/plapre-nano-v2@007e0b471e377dd5061786f7df6ad659d15c4d5f` | CC BY 4.0; public and ungated at verification | 1,340,073,504-byte fp32 model plus `speaker_proj.pt` |
| Plapre code | `syv-ai/plapre@b111239d3b099cfafcb54b3471a8e9e9ba71ae8b` | `pyproject.toml` declares MIT; repository has no checked-in `LICENSE` file at this pin | Custom prompt-embedding generation, speaker handling, decoder and HTTP server |
| Kanade code | `frothywater/kanade-tokenizer@961f20bf892c59f391d0b6c5f7b88e70ed919b99` | package metadata does not declare a license | Installed through an unpinned Git dependency in Plapre's `pyproject.toml` |
| Kanade codec | `frothywater/kanade-25hz-clean@cb2c8f10959ff0e5d3e97c9b82fcc3779c9532a5` | MIT; public/ungated | 562,711,836-byte audio codec; hard-coded by model ID in Plapre |
| HiFT vocoder | `FunAudioLLM/CosyVoice2-0.5B@eec1ae6c79877dbd9379285cf8789c9e0879293d`, file `hift.pt` SHA-256 `3386cc880324d4e98e05987b99107f49e40ed925b8ecc87c1f4939432d429879` | Apache-2.0; public/ungated | 83,390,254-byte vocoder downloaded by Kanade from a mutable default revision |

Artifact sources: [pinned Plapre model API including LFS hashes and
sizes](https://huggingface.co/api/models/syvai/plapre-nano-v2/revision/007e0b471e377dd5061786f7df6ad659d15c4d5f?blobs=true),
[pinned Plapre source tree](https://github.com/syv-ai/plapre/tree/b111239d3b099cfafcb54b3471a8e9e9ba71ae8b),
[pinned Kanade source tree](https://github.com/frothywater/kanade-tokenizer/tree/961f20bf892c59f391d0b6c5f7b88e70ed919b99),
[pinned Kanade model API](https://huggingface.co/api/models/frothywater/kanade-25hz-clean/revision/cb2c8f10959ff0e5d3e97c9b82fcc3779c9532a5?blobs=true),
[pinned HiFT artifact API](https://huggingface.co/api/models/FunAudioLLM/CosyVoice2-0.5B/revision/eec1ae6c79877dbd9379285cf8789c9e0879293d?blobs=true).

The v2 model itself no longer needs gated-repository acceptance: its current
Hugging Face API record is public and ungated. The Plapre README's prerequisite
section names the gated v1 `plapre-nano` and `plapre-pico` models, not Nano v2.
[Pinned v2 model card](https://huggingface.co/syvai/plapre-nano-v2/blob/007e0b471e377dd5061786f7df6ad659d15c4d5f/README.md),
[pinned Plapre prerequisites](https://github.com/syv-ai/plapre/blob/b111239d3b099cfafcb54b3471a8e9e9ba71ae8b/README.md#prerequisites).

### Reproducibility gap

`plapre-serve --checkpoint` accepts a model ID or local path but has no
`--revision` argument. The library's Kanade and HiFT calls also omit revisions,
and the Kanade package is installed from Git without a commit in Plapre's own
dependency declaration. A deployment is not immutable merely because
`PLAPRE_CHECKPOINT=syvai/plapre-nano-v2` is set.

Build a derived image from the NVIDIA ARM64 digest, install both Git packages at
the commits above without upgrading the base vLLM/torch stack, fetch each model
at its exact revision, and run from local snapshots with network-disabled/offline
cache verification. The current upstream source needs a small pinning patch (or
a fully materialized, content-addressed image) for Kanade and HiFT; otherwise
those two downloads still follow mutable defaults. This is a deployment-code
requirement, not implemented in this research note. Sources: [Plapre loader](https://github.com/syv-ai/plapre/blob/b111239d3b099cfafcb54b3471a8e9e9ba71ae8b/plapre/inference.py),
[Kanade model loader](https://github.com/frothywater/kanade-tokenizer/blob/961f20bf892c59f391d0b6c5f7b88e70ed919b99/src/kanade_tokenizer/model.py#L368-L408),
[Kanade HiFT downloader](https://github.com/frothywater/kanade-tokenizer/blob/961f20bf892c59f391d0b6c5f7b88e70ed919b99/src/kanade_tokenizer/util.py#L67-L85).

## Server command and actual HTTP contract

Plapre is not served by vLLM's OpenAI server. Its library creates an embedded
vLLM engine with `enable_prompt_embeds=True`, prepends a projected speaker
embedding, generates Kanade tokens, and decodes them with Kanade plus HiFT.
Start the publisher's FastAPI application instead:

```bash
PLAPRE_ENFORCE_EAGER=1 \
  plapre-serve \
  --host 127.0.0.1 \
  --port 8004 \
  --checkpoint /models/plapre-nano-v2/007e0b471e377dd5061786f7df6ad659d15c4d5f \
  --gpu-mem 0.06 \
  --max-model-len 512
```

`0.06` is a **first coexistence trial**, not a qualified value. Eager mode avoids
CUDA-graph memory on a tight shared GPU. Keep the service on loopback or a
private container network: the server has no authentication and its CLI default
bind is `0.0.0.0`. Source: [pinned server and CLI](https://github.com/syv-ai/plapre/blob/b111239d3b099cfafcb54b3471a8e9e9ba71ae8b/plapre/server.py),
[pinned engine construction](https://github.com/syv-ai/plapre/blob/b111239d3b099cfafcb54b3471a8e9e9ba71ae8b/plapre/inference.py#L125-L213).

The implemented API is:

```http
POST /v1/audio/speech
Content-Type: application/json

{
  "text": "Hej, hvordan har du det?",
  "speaker": "tor",
  "temperature": 0.8,
  "top_p": 0.95,
  "top_k": 50,
  "max_tokens": 500
}
```

It returns chunked raw signed 16-bit little-endian PCM, 24 kHz, mono, with
`Content-Type: audio/pcm` and `X-Sample-Rate`, `X-Channels`, and `X-Bit-Depth`
headers. `GET /health` returns `{"status":"ok"}` and `GET /v1/speakers`
returns the loaded speaker IDs. The request is sentence-split, but current code
finishes token generation for every sentence before it starts decoding and
yielding audio, so chunked HTTP does not imply immediate first-sentence token
streaming. [Pinned server implementation](https://github.com/syv-ai/plapre/blob/b111239d3b099cfafcb54b3471a8e9e9ba71ae8b/plapre/server.py#L51-L139).

This endpoint is **path-compatible but not schema-compatible** with OpenAI's
speech endpoint. OpenAI requires `model`, `input`, and `voice` and supports a
`response_format`; Plapre expects `text` and optional `speaker` and always emits
raw PCM. An OpenAI SDK, LiteLLM audio route, or OpenAI-to-Wyoming bridge cannot
call it unchanged. [OpenAI speech API contract](https://platform.openai.com/docs/api-reference/audio/voice-consent-list?lang=curl),
[Plapre request model](https://github.com/syv-ai/plapre/blob/b111239d3b099cfafcb54b3471a8e9e9ba71ae8b/plapre/server.py#L51-L59).

## Voice and reference-audio requirements

The model always needs a 128-dimensional speaker embedding. The package ships
five embedded speaker IDs: `tor`, `ida`, `liv`, `ask`, and `kaj`; absent a
speaker, `tor` is the default because it is first in `speakers.json`. The pinned
README's HTTP example uses `"speaker":"mic"`, but no `mic` speaker exists at
this pin, so that request returns HTTP 400. Discover speakers from
`GET /v1/speakers` and use an advertised ID. Sources: [pinned speaker embeddings](https://github.com/syv-ai/plapre/blob/b111239d3b099cfafcb54b3471a8e9e9ba71ae8b/plapre/speakers.json),
[speaker resolution](https://github.com/syv-ai/plapre/blob/b111239d3b099cfafcb54b3471a8e9e9ba71ae8b/plapre/inference.py#L813-L835),
[mismatching README example](https://github.com/syv-ai/plapre/blob/b111239d3b099cfafcb54b3471a8e9e9ba71ae8b/README.md#generate-speech).

For a supplied reference clip, the Python API accepts any SoundFile-readable
audio, converts it to float32, resamples to 24 kHz, and averages multiple
channels to mono before Kanade extracts content tokens and the global speaker
embedding. `clone()` accepts `reference_wav`; its transcript is optional but
helpful. The model card gives no minimum duration, loudness, noise, or file
format requirement, so those must be established by local voice qualification.
Long reference controls may need `max_model_len=1536`. The current HTTP server
does **not** accept a reference-audio upload or path; it only selects a packaged
speaker ID. A production approved reference voice therefore needs offline
embedding registration behind an allow-list or a deliberately extended server,
not an arbitrary client-supplied filesystem path. Sources: [reference-audio conversion](https://github.com/syv-ai/plapre/blob/b111239d3b099cfafcb54b3471a8e9e9ba71ae8b/plapre/inference.py#L522-L546),
[model controls and requirements](https://huggingface.co/syvai/plapre-nano-v2/blob/007e0b471e377dd5061786f7df6ad659d15c4d5f/README.md#what-it-can-do).

Licensing and voice permission are separate:

- The model card marks the checkpoint CC BY 4.0. Distribution or sharing must
  preserve appropriate credit, link the license, and indicate modifications.
  CC BY permits commercial use, but Creative Commons explicitly warns that
  publicity, privacy, and moral rights can require additional permission.
- Upstream does not document the identity, source recording license, or consent
  status of the five packaged speaker embeddings. Do not make them the household
  default until provenance is recorded.
- For a custom family/member voice, retain explicit subject consent, permitted
  uses, source-audio license, retention/deletion terms, and the hash of the
  approved clip/embedding. Do not expose arbitrary voice cloning through Home
  Assistant.

Sources: [pinned model card license](https://huggingface.co/syvai/plapre-nano-v2/blob/007e0b471e377dd5061786f7df6ad659d15c4d5f/README.md),
[CC BY 4.0 terms and other-rights warning](https://creativecommons.org/licenses/by/4.0/).

## Home Assistant integration boundary

### Preferred: a narrow Wyoming TTS adapter

Home Assistant's supported external local voice boundary is Wyoming. An
external service is added by hostname/IP and port, and Home Assistant's Wyoming
TTS provider consumes service metadata, voice/speaker selection, and PCM audio
chunks. Implement or pin a small adapter that:

1. advertises one Danish TTS model, `da`, and only approved speaker aliases;
2. accepts Wyoming `synthesize` plus the streaming
   `synthesize-start/chunk/stop` lifecycle used by current Home Assistant;
3. maps the approved alias to Plapre's private `speaker` ID;
4. forwards Plapre's 24 kHz, 16-bit, mono PCM as Wyoming `audio-start`, ordered
   `audio-chunk` events, and `audio-stop`; and
5. keeps both unauthenticated protocols on a trusted VLAN/private container
   network because Wyoming intentionally provides neither authentication nor
   encryption.

Sources: [Home Assistant external Wyoming service setup](https://www.home-assistant.io/integrations/wyoming/),
[Wyoming protocol TTS and PCM event contract](https://github.com/OHF-Voice/wyoming/blob/master/README.md),
[Home Assistant Wyoming TTS implementation](https://github.com/home-assistant/core/blob/dev/homeassistant/components/wyoming/tts.py).

### Optional: normalize Plapre to OpenAI first

A reusable internal adapter can expose a true `POST /v1/audio/speech` contract:
map `input` to `text`, validate a stable `model` alias, map a `voice` allow-list
to a Plapre speaker, accept only `response_format=pcm`, and return the raw PCM
stream. That makes Plapre usable by normal OpenAI-compatible clients. A project
such as `wyoming_openai` can then translate OpenAI-compatible TTS to Wyoming,
including current streaming TTS events, but it cannot sit directly in front of
unmodified Plapre because of the schema mismatch. [wyoming_openai's own
documented contract](https://github.com/roryeckel/wyoming_openai),
[OpenAI PCM stream format](https://developers.openai.com/api/docs/guides/text-to-speech).

For Home Assistant alone, a direct Wyoming adapter has fewer moving parts. If
n8n, Open WebUI, or another client also needs TTS, make the strict OpenAI shim
the private canonical boundary and put Wyoming in front of it. The built-in
Home Assistant OpenAI conversation integration is not a generic local TTS
provider; TTS is a separate stage/entity in the Assist pipeline. [Home
Assistant voice architecture](https://developers.home-assistant.io/docs/voice/overview/),
[TTS entity contract](https://developers.home-assistant.io/docs/core/entity/tts/).

## Can it coexist with resident Qwen3.8-27B?

**Likely yes, but not with Plapre's documented standalone defaults and not yet
proven on this host.**

The pinned Qwen artifacts total about 23.4 GB on disk, while Plapre's headline
model, Kanade model, and HiFT file total about 1.99 GB. Runtime memory is higher
than file size, but Plapre remains small relative to the resident 27B model.
More importantly, this repository caps Qwen's vLLM instance at 40% of unified
memory. vLLM 0.15 documents `gpu_memory_utilization` as a per-instance limit for
weights, activations, and KV cache. A first Plapre trial at 6% makes the two
vLLM reservations 46% of the 128 GiB pool, before non-vLLM allocations; this is
a plausible coexistence envelope with substantial system headroom. Sources:
[pinned Qwen artifact sizes](https://huggingface.co/api/models/unsloth/Qwen3.8-27B-NVFP4/revision/57926baca9a82b4d6906b43f2750d55315f5b10f?blobs=true),
[pinned speech artifact APIs](https://huggingface.co/api/models/syvai/plapre-nano-v2/revision/007e0b471e377dd5061786f7df6ad659d15c4d5f?blobs=true),
[vLLM 0.15 cache semantics](https://docs.vllm.ai/en/v0.15.0/api/vllm/config/cache/),
[repository Qwen envelope](../../config/compute-node.env.example).

Do not copy the README's `--gpu-mem 0.5` example: combined with Qwen's `0.40`,
it asks the two vLLM instances to reserve up to 90% of the unified pool, leaving
too little confidence for the Grace CPU, OS, containers, decoder, transient
activations, and other speech services. NVIDIA separately warns that aggressive
vLLM allocation can OOM on DGX Spark's unified memory. [Plapre memory example](https://github.com/syv-ai/plapre/blob/b111239d3b099cfafcb54b3471a8e9e9ba71ae8b/README.md#gpu-memory),
[NVIDIA unified-memory warning](https://docs.nvidia.com/deeplearning/frameworks/vllm-release-notes/rel-26-02.html#known-issues).

Qualification must still measure cold start, warm p95 first audio, RTF, peak
unified memory, Qwen TTFT/throughput regression, concurrent synthesis, repeated
multi-sentence looping, service restart, and OOM recovery. Begin at 0.06,
`max_model_len=512`, eager mode, and one synthesis at a time; increase the
Plapre fraction only if startup or the target concurrency needs it. Promotion
requires the repository's existing mixed-load and Danish listening gates, not
capacity arithmetic alone.

## Implementation hand-off

1. Build the isolated pinned ARM64 image and close the Kanade/HiFT revision gap.
2. Register one consented, approved Danish voice alias; do not expose arbitrary
   cloning or unreviewed built-in speakers.
3. Add the private OpenAI-normalizing adapter if multiple clients need TTS;
   otherwise implement the narrower Wyoming adapter directly.
4. Run coexistence and quality qualification with Qwen3.8-27B resident.
5. Only then configure Home Assistant's Wyoming integration and Assist pipeline.
