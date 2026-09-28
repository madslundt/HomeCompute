# Compute-node deployment

`compose.yaml` defines the text baseline and staged modality services for
`home-spark`. With no profile, Compose starts `text-primary` plus its small,
credential-free `text-edge` TCP relay.
The existing `prepare` profile still contains only the token-authenticated
`model-fetch` job for text.

## Observed live allocation (2026-09-26)

The active text allocation differs from the default no-profile startup:

- Qwen3.6-35B-A3B NVFP4 is healthy as `automation-moe` and serves four
  published n8n workflows;
- Gemma 4 E4B QAT W4A16 is healthy as the separate `home-fast` service;
- Qwen3.8-27B is stopped because the automation lifecycle performs an
  exclusive cold swap; and
- Flash-Next remains stopped.

Hviske v5.3 and Plapre Nano v2 are also healthy. Plapre's Wyoming adapter
applies a fixed `1.20x` pitch-preserving tempo. The current transport to
`home-core` is the restricted SSH fallback, not the dedicated compute NIC.

Do not run `deactivate` while n8n still targets `automation-moe`: the cached
home-core standby currently protects only the separate `automation` alias and
will not receive those requests automatically.

The text service is the first final-roster deployment stage: pinned
`unsloth/Qwen3.8-27B-NVFP4` on vLLM with native `qwen3_5_mtp`. The current
selection authority is
[`config/gb10-model-roster.json`](../../config/gb10-model-roster.json).
The normal lane disables thinking in the server's default chat-template kwargs;
qualified callers can still request it explicitly.
The general Qwen3.8 process serves only the internal deployment name
`general-spark-qwen38`; it does not publish public capability aliases. Its
guarded smoke command checks required and automatic function selection for
that deployment. The separate registry-backed LiteLLM config omits this
intentionally stopped deployment from active routes. The dedicated
`home-fast` Gemma 4 process and the opt-in Qwen3.6 `automation-moe` process
have their matching `gemma4` and `qwen3_coder` parsers and run the same two
selection checks. The `assistant-canary` gateway alias maps to the same
`automation-moe` process and parser; it does not introduce another model tuple.
The staged SGLang/DFlash alternative records the same Qwen3.8 parser contract
in the model roster, but it has no serving profile yet and remains unverified.
Flash-Next likewise has no routable serving profile, so it is not represented
as a tool-capable route.

The planned-maintenance CPU standby uses llama.cpp's model chat template via
`--jinja`; its smoke checks both tool-choice modes directly. It remains a cold,
operator-started maintenance route and is not listed as request-path automatic
failover.
Function selection is model behavior, so a successful parser/template
configuration alone does not guarantee a call. The smoke commands require a
returned function name and valid JSON arguments and fail if either mode returns
ordinary text instead. Re-run them whenever the pinned model, serving image, or
chat template changes.

SGLang/DFlash and Flash-Next need separate pinned runtime profiles because
their images, drafts, memory behavior, and startup contracts are not
interchangeable with this launcher.

The `prepare-automation` and `automation-moe` profiles add the pinned
`unsloth/Qwen3.6-35B-A3B-NVFP4` n8n candidate. It uses the current pinned
NVIDIA vLLM 26.08 image (vLLM 0.27.1), `sm_121a`, the
native `cutlass` NVFP4 MoE backend, a portable `triton` override for the
model's mixed FP8 expert path, `qwen3_coder` tool parser, 128K context, and
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

## NVIDIA ModelOpt qualification candidate

`setup-compute-automation-nvidia.sh` stages the pinned
`nvidia/Qwen3.6-35B-A3B-NVFP4` revision separately from the Unsloth cache and
uses the isolated vLLM 0.28.0 ARM64 image. Its GB10 recipe enables Marlin,
FlashInfer, FP8 KV, and three-token MTP. It runs on the same exclusive port
8005 during a cold-swap window and is exposed only as `automation-moe-nvidia`.

The script records whether `automation-primary` (qualified Unsloth) or
`text-primary` (Qwen3.8) was running before activation. Startup or baseline
smoke failure restores that exact service; `deactivate` restores it after the
qualification window. Existing aliases that target the stopped source model
are unavailable during the swap. `automation` and `automation-moe` routes are
not promoted by this script. Complete the full qualification gates before
changing production routes.

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

The repository also includes a constrained sparkrun JSON adapter at
[`scripts/sparkrun-model-manager.py`](../../scripts/sparkrun-model-manager.py)
for a future authenticated `home-core` model dashboard. It generates only
local recipes from the canonical model catalog and reuses the accepted-cache
and qualification workflows. The current Compose services remain production
owners of their ports, so a sparkrun load is refused while one of those ports
is occupied. No live workloads are switched by adding or deploying this
adapter. Sparkrun does not currently expose all Compose hardening settings;
qualify its container security and gateway routing before migration.

To provision the fixed dashboard access boundary, Sparkrun must already be
securely installed and configured for root on `home-spark`. Its root-owned,
non-writable executable must be `/usr/local/bin/sparkrun` or
`/root/.local/bin/sparkrun`, and this command must work as root:

```bash
sudo /usr/local/bin/sparkrun cluster show home-spark --json
```

If Sparkrun is installed at `/root/.local/bin/sparkrun`, use that path for the
check. The official installation command is `uvx sparkrun setup install`; use
the [Sparkrun setup wizard](https://sparkrun.dev/getting-started/setup-wizard/)
to configure the root-owned `home-spark` cluster first. Protect the root
Sparkrun configuration and credentials. Provision a
dedicated Ed25519 key on `home-core`, copy only its `.pub` file to
`/etc/gb10-ai/secrets/sparkrun-manager.pub` on Spark, and make that source
`root:root` mode `0600`. For example, with an existing `home-spark` SSH alias:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/homecompute-sparkrun-manager \
  -C sparkrun-manager@home-core
scp ~/.ssh/homecompute-sparkrun-manager.pub home-spark:/tmp/sparkrun-manager.pub
ssh home-spark 'sudo install -o root -g root -m 0600 /tmp/sparkrun-manager.pub /etc/gb10-ai/secrets/sparkrun-manager.pub && rm -f /tmp/sparkrun-manager.pub'
ssh home-spark 'sudo /srv/homecompute/current/scripts/setup-compute-sparkrun-manager.sh validate'
ssh home-spark 'sudo /srv/homecompute/current/scripts/setup-compute-sparkrun-manager.sh install'
```

Provisioning installs a locked `sparkrun-manager` SSH account, a single forced
`authorized_keys` command, the root-owned no-argument wrapper
`/usr/local/sbin/homecompute-sparkrun-model-manager`, and one exact no-argument
NOPASSWD sudo rule. The SSH key can submit only the adapter's JSON actions;
catalog IDs and all recipes/runtime settings remain server-side. The helper
does not install/configure Sparkrun, generate keys, use private-key material,
or expose an HTTP listener. `home-core` should keep the private client key
readable only to the model-manager service identity.

Generated Sparkrun recipes use host networking with vLLM bound to
`127.0.0.1`, matching the current home-core SSH loopback tunnel. Do not change
that bind to a Spark LAN address until the direct-link route, firewall,
authentication, and gateway path have been separately qualified. Sparkrun's
Docker executor still lacks the full Compose hardening tuple, including a
read-only root filesystem and dropping every Linux capability.

Changing an image, model, revision, tokenizer, template, parser, quantization,
context, backend, adapter, voice, or decoding setting creates a new tuple that
must pass the full qualification suite.
