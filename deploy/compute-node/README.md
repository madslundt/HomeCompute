# Compute-node deployment

`compose.yaml` defines the text baseline and staged modality services for
`home-spark`. With no profile, Compose starts `text-primary` plus its small,
credential-free `text-edge` TCP relay.
The existing `prepare` profile still contains only the token-authenticated
`model-fetch` job for text.

The text service is the first final-roster deployment stage: pinned
`unsloth/Qwen3.8-27B-NVFP4` on vLLM with native `qwen3_5_mtp`. The current
selection authority is
[`config/gb10-model-roster.json`](../../config/gb10-model-roster.json).
The normal lane disables thinking in the server's default chat-template kwargs;
qualified callers can still request it explicitly.
SGLang/DFlash and Flash-Next need separate pinned runtime profiles because
their images, drafts, memory behavior, and startup contracts are not
interchangeable with this launcher.

The `prepare-automation` and `automation-moe` profiles add the pinned
`unsloth/Qwen3.6-35B-A3B-NVFP4` n8n candidate. It uses the current pinned
NVIDIA vLLM 26.08 image (vLLM 0.27.1), `sm_121a`, the
native `cutlass` NVFP4 MoE backend, a portable `triton` override for the
model's mixed FP8 expert path, `qwen3_coder` tool parser, 64K context, and
non-thinking defaults. MTP is deliberately disabled for the correctness
baseline. The candidate has its own accepted cache manifest, edge relay, API
port 8005, and `automation-moe` served name. It is excluded from default
Compose startup and never replaces the ordinary `automation` alias until the
Danish, structured-output, 64-tool, n8n replay, memory, and recovery gates pass.

Use the guarded lifecycle rather than starting its Compose profile directly:

```bash
sudo ./scripts/setup-compute-automation-moe.sh install
sudo ./scripts/setup-compute-automation-moe.sh activate
sudo ./scripts/setup-compute-automation-moe.sh deactivate
```

`activate` is a single-resident cold swap: it stops Qwen3.8-27B before loading
the MoE and restores Qwen3.8 automatically if startup or smoke fails.
`deactivate` stops the MoE and restores Qwen3.8. Flash-Next is not part of
either path and remains off by default.

The account-free `prepare-modalities` profile is a legacy, pre-decision
scaffold. It acquires these public artifacts at full publisher revisions:

- Qwen3-VL-Embedding-2B (Apache-2.0);
- Phi-4-multimodal-instruct and its `vision-lora` directory (MIT);
- Whisper large-v3-turbo (MIT), staged for Danish/English qualification; and
- the `da_DK-talesyntese-medium` Piper voice, config, and model card from the
  pinned voice repository revision (CC0-1.0 dataset).

These modality checkpoints are not in the final roster. Do not use this
profile for the new speech deployment. It remains as historical scaffolding
until dedicated Hviske, Parakeet, Plapre, and Qwen3-TTS services have pinned
runtime images and equivalent acquisition, authentication, and smoke tests.

The fetch job receives no registry token. Before each Hugging Face download,
the bounded fetch helper resolves the exact commit metadata and rejects tuples
above their reviewed byte or file-count ceilings. A parent process bounds the
tuple's actual allocated-byte growth, total 500 GiB/50,000-file cache
allocation, and 200 GiB free-space reserve. Both fetch containers are capped at
4 CPUs, 8 GiB memory, and 256 PIDs. Hugging Face snapshots persist only in the
configured Hugging Face cache. Piper persists only its model, config, and model
card beneath
`GB10_ROOT/models/piper`, after their exact byte lengths and all three
configured SHA-256 digests pass. The modality setup script then atomically
accepts separate `accepted-embedding-cache.json`,
`accepted-vision-cache.json`, and `accepted-stt-cache.json` manifests.

Individual `embedding`, `vision`, `stt`, and `tts` profiles render the legacy
runtime services below. The explicit `modalities` profile starts all five only
for reproducing the earlier qualification work:

| Service | Private port | Interface |
| --- | ---: | --- |
| `embedding-primary` | 8001 | OpenAI `/v1/embeddings`, model `embedding` |
| `vision-primary` | 8002 | OpenAI `/v1/chat/completions`, model `vision` |
| `stt-primary` | 8003 | OpenAI `/v1/audio/transcriptions`, model `stt` |
| `tts-primary` | 10200 | Wyoming TCP, pinned Danish Piper voice |
| `tts-openai-adapter` | 8004 | OpenAI `/v1/audio/speech`, WAV output |

These services use immutable image and artifact identities, a read-only root
filesystem, dropped capabilities, no-new-privileges, bounded resources and
logs, and loopback-by-default publishing. Each HTTP service reads the shared
API key from its external secret file. Wyoming has no application authentication,
so it must stay on loopback until the qualified private address and
source-restricted firewall are explicitly enabled. Install
copies the cache verifier and TTS adapter into a root-owned runtime directory;
containers never execute mutable files from the checkout, and release records
capture both program digests. Model runtime containers have only the internal
inference network. Docker 29 does not publish ports for a container attached
solely to an internal bridge, so `text-edge` attaches to both the internal and
host-published edge networks and forwards raw TCP without parsing or logging
requests. It has no model/API credential and runs read-only with dropped
capabilities and bounded resources. Hugging Face is forced offline, accepted
cache manifests are re-verified before vLLM starts, and Piper re-verifies every
voice-file digest before serving.

Vision is a rendered-page service, not a document parser. Clients must render
each PDF/document page to an image and send inline image data through
`/v1/chat/completions`; native PDF input and server-side remote media fetching
are not accepted. All modalities remain
staged and are not production-qualified until the live `home-spark` appliance
checks pass.

The vLLM API key protects the OpenAI-compatible paths, not every endpoint on
the processes; request-content and Uvicorn access logging are disabled. For a
private listener, install the persistent repository-owned `DOCKER-USER`
original-connection policy. It allows only `home-core` to the exact published
address and `COMPUTE_HOST_PORTS` set. The gateway must also expose only its
route allow-list. See the
[vLLM security guidance](https://docs.vllm.ai/en/stable/usage/security/).

Do not run the Compose file directly for an installation. Use
[`setup-compute-node.sh`](../../scripts/setup-compute-node.sh) for normal text,
the automation-MoE lifecycle for its opt-in cold swap, and the staged modality
lifecycle script for modality preparation and deployment.
They validate the release tuple, host, paths, service identity, secrets, bind
policy, artifact integrity, and provenance.

Changing an image, model, revision, tokenizer, template, parser, quantization,
context, backend, adapter, voice, or decoding setting creates a new tuple that
must pass the full qualification suite.
