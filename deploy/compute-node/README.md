# Compute-node deployment

`compose.yaml` defines the text baseline and staged modality services for
`home-spark`. With no profile, Compose still starts only `text-primary`.
The existing `prepare` profile still contains only the token-authenticated
`model-fetch` job for text.

The text service is still the legacy Qwen3.6 integration tuple and must not be
mistaken for the selected model roster. The current roster is
[`config/gb10-model-roster.json`](../../config/gb10-model-roster.json). Its
Flash-Next, Nemotron Lightning, and Qwen3.8-27B candidates need separate pinned
runtime profiles because their recipes, images, drafts, memory behavior, and
startup contracts are not interchangeable. Until those profiles pass live
GB10 qualification, do not edit only `MODEL_ID` in the existing environment.

The account-free `prepare-modalities` profile runs the one-shot
`modality-fetch` service. It acquires these public artifacts at full publisher
commit revisions:

- Qwen3-VL-Embedding-2B (Apache-2.0);
- Phi-4-multimodal-instruct and its `vision-lora` directory (MIT);
- Whisper large-v3-turbo (MIT), staged for Danish/English qualification; and
- the `da_DK-talesyntese-medium` Piper voice, config, and model card from the
  pinned voice repository revision (CC0-1.0 dataset).

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

Individual `embedding`, `vision`, `stt`, and `tts` profiles render the hardened
runtime services below. The explicit `modalities` profile starts all five only
for a mixed-load qualification run:

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
capture both program digests. Runtime containers have only the internal
inference network: Hugging Face is forced offline, accepted cache manifests are
re-verified before vLLM starts, and Piper re-verifies every voice-file digest
before serving.

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
[`setup-compute-node.sh`](../../scripts/setup-compute-node.sh) for text and the
staged modality lifecycle script for modality preparation and deployment.
They validate the release tuple, host, paths, service identity, secrets, bind
policy, artifact integrity, and provenance.

Changing an image, model, revision, tokenizer, template, parser, quantization,
context, backend, adapter, voice, or decoding setting creates a new tuple that
must pass the full qualification suite.
