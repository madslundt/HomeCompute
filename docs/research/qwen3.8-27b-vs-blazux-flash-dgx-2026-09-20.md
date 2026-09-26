# Qwen3.8-27B resident service vs. the pinned Blazux Flash-DGX recipe

Verified: 2026-09-20

Scope: compare the repository's normal service with the exact operator-only
heavy-mode recipe on one 128 GiB GB10 / DGX Spark-class host. This is a
deployment-risk comparison, not an on-device benchmark. Blazux performance
claims are project-authored measurements, not independent certification.

## Recommendation

**Keep `unsloth/Qwen3.8-27B-NVFP4` plus native vLLM MTP as the normal,
always-available choice. Keep the pinned Blazux lane as a drained,
operator-controlled heavy-mode experiment; do not make it the resident default.**

Flash-Next is the higher capability ceiling on paper, but no primary source
provides a controlled comparison of the exact Unsloth runtime tuple and this
pinned RadixArk-plus-Blazux tuple on our workloads. Blazux depends on custom
vLLM patches, mmap-backed NVMe access and a much tighter memory envelope. That
is the wrong failure profile for the shared home/automation/coding service.

Promote Flash-Next only if it wins local accepted-task A/B tests and passes
cold-start/restart, 24-hour mixed load, long context, prefix cache, tool/JSON,
Danish and memory-headroom gates behind this repository's ingress controls.

## What is actually selected

| | Normal service | Pinned heavy mode |
| --- | --- | --- |
| Exact model | `unsloth/Qwen3.8-27B-NVFP4` at `57926baca9a82b4d6906b43f2750d55315f5b10f` | `RadixArk/Qwen3.8-Flash-Next-NVFP4` at `7b719225242aacd3dbd3f9407468c2ee9a9d2594` |
| Runtime | NVIDIA vLLM image pinned by digest; native `qwen3_5_mtp`, 3 speculative tokens | Blazux at `2d9b5b744b8b8e25e0c40120f4a13bcec3554aa9`; derived vLLM image plus 9 patches |
| Intended role | Resident workhorse: assistant, automation, coding, home, meeting, research | Exclusive cold-swap lane: coding and research; stop normal model first |
| Initial envelope | 64K context, 2 sequences, 40% unified-memory utilization; model native maximum 262K | 262K context, 8 sequences, 80% memory fraction, MTP=2, prefix cache; optional YaRN to 500K |
| Artifact scale | 22.57 GB main safetensors + 849 MB MTP at the pin | About 126 GiB checkpoint; 48 GiB PLE/n-gram table mmap'd from NVMe, with about 76 GiB reported resident weights |

The normal tuple deliberately pins model, tokenizer, remote code, chat template,
runtime image, parsers, service account and private ingress. It rejects a
different model or speculative configuration at setup time. [Normal-service
configuration](../../config/compute-node.env.example), [setup enforcement](../../scripts/setup-compute-node.sh),
[model roster](../../config/gb10-model-roster.json), [ADR-019](../adr/019-single-gb10-model-roster.md).

The 27B card confirms a dense 27B model with native MTP and 262K native context;
the pinned NVFP4 card says it works with vLLM and is Apache-2.0. [Pinned Unsloth
card](https://huggingface.co/unsloth/Qwen3.8-27B-NVFP4/blob/57926baca9a82b4d6906b43f2750d55315f5b10f/README.md),
[pinned artifact tree](https://huggingface.co/api/models/unsloth/Qwen3.8-27B-NVFP4/revision/57926baca9a82b4d6906b43f2750d55315f5b10f?blobs=true),
[vLLM MTP documentation](https://docs.vllm.ai/en/latest/features/speculative_decoding/mtp/).

The RadixArk card describes a 48-layer multimodal Flash-Next MoE: 512 routed
experts (top-10), one MTP layer, routed experts in NVFP4 and attention/PLE/MTP
at higher precision. It is licensed `other` and defers terms to the source
model. [Pinned RadixArk card](https://huggingface.co/RadixArk/Qwen3.8-Flash-Next-NVFP4/blob/7b719225242aacd3dbd3f9407468c2ee9a9d2594/README.md).

## Why Blazux is materially different

Blazux is not an alternative `--model` value. Its pinned README says the 126
GiB checkpoint cannot coexist with useful KV cache in GB10's 128 GiB pool. It
modifies vLLM to mmap PLE from NVMe and patches GB10 Flash Linear Attention,
Mamba state copy/prefix caching, QSA top-k, optional FP8 side layers and
optional FP8 KV. Piecewise CUDA graphs are needed because PLE lookup includes a
CPU/pageable-host-to-device step. [Recipe overview](https://github.com/blazux/qwen3.8-Flash-DGX/blob/2d9b5b744b8b8e25e0c40120f4a13bcec3554aa9/README.md#L1-L20),
[derived image and patches](https://github.com/blazux/qwen3.8-Flash-DGX/blob/2d9b5b744b8b8e25e0c40120f4a13bcec3554aa9/Dockerfile#L19-L134),
[launch script](https://github.com/blazux/qwen3.8-Flash-DGX/blob/2d9b5b744b8b8e25e0c40120f4a13bcec3554aa9/scripts/serve.sh#L86-L135).

This is valuable feasibility engineering, but it adds NVMe/page-cache, custom
runtime, and tight-memory failure domains. The pin reports swapping after a day
at `.85` and an OOM kill at `.875` during a 300K prefill; `.80` is default. It
reports about 26 tok/s NVFP4 and 31 tok/s hybrid single-stream with MTP=2,
which cannot be compared to the unmeasured normal tuple. [Memory caveats](https://github.com/blazux/qwen3.8-Flash-DGX/blob/2d9b5b744b8b8e25e0c40120f4a13bcec3554aa9/README.md#L260-L280),
[reported measurements](https://github.com/blazux/qwen3.8-Flash-DGX/blob/2d9b5b744b8b8e25e0c40120f4a13bcec3554aa9/README.md#L92-L103).

## Reliability and security

The pin fixes real GB10 correctness issues: prefix-cache hits could restore an
all-zero Mamba state, while stock QSA top-k could be non-deterministic and drop
candidates. That improves the recipe, but shows why every image/model update
needs regression tests. [Prefix-cache analysis](https://github.com/blazux/qwen3.8-Flash-DGX/blob/2d9b5b744b8b8e25e0c40120f4a13bcec3554aa9/README.md#L181-L198),
[QSA analysis](https://github.com/blazux/qwen3.8-Flash-DGX/blob/2d9b5b744b8b8e25e0c40120f4a13bcec3554aa9/README.md#L200-L231).

Do **not** deploy its `serve.sh` unchanged on the LAN/WAN. It publishes a port,
starts vLLM on `0.0.0.0`, uses host IPC, and read-write mounts the full Hugging
Face cache. The pin has no API key by default and deliberately expands `EXTRA`
as vLLM arguments. Use a service account, loopback/private bind, authenticated
gateway, and narrower read-only model mount. [Pinned serve implementation](https://github.com/blazux/qwen3.8-Flash-DGX/blob/2d9b5b744b8b8e25e0c40120f4a13bcec3554aa9/scripts/serve.sh#L114-L135),
[recipe security note](https://github.com/blazux/qwen3.8-Flash-DGX/blob/2d9b5b744b8b8e25e0c40120f4a13bcec3554aa9/README.md#L445-L460).

## Upstream moved after our pin

At verification, `main` was `5be66376e8beaf96655f2d5682c82d538a970e66`
(2026-09-18), **42 commits ahead** of our 2026-09-08 pin. Current upstream
switched its default from RadixArk to NVIDIA's Flash-Next checkpoint and added
a vLLM 0.29 route, download completeness/Xet handling, profiles, metrics,
compile-cache work, and tool-marker/parser patches. Its own comparison favours
the NVIDIA checkpoint for quality and KV capacity at a modest decode tradeoff.
This justifies revisiting the heavy lane, not silently moving its pin. [GitHub
comparison](https://github.com/blazux/qwen3.8-Flash-DGX/compare/2d9b5b744b8b8e25e0c40120f4a13bcec3554aa9...main),
[current recommendation](https://github.com/blazux/qwen3.8-Flash-DGX#qwen38-flash-next-on-a-single-dgx-spark-gb10),
[current profiles](https://github.com/blazux/qwen3.8-Flash-DGX/tree/main/profiles).

## Decision rule

1. Qualify and use the compact 27B service for normal traffic.
2. For deep coding/research, drain the GB10 and run the immutable heavy pin only
   behind the secure wrapper; record task success, TTFT, throughput, peak memory,
   NVMe/page-cache behavior, restart time and recovery.
3. Treat current Blazux `main` as a separate candidate: its default model, image
   path and parser/runtime behavior differ, so it needs new provenance review
   and full qualification before a roster change.

