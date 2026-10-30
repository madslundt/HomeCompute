# Home-spark runtime source notes — 2026-10-29

Research support for the appliance optimization plan. No host commands, model
loads, runtime changes, or benchmarks were executed for this source review.
Runtime facts below describe pinned source unless explicitly corroborated by the
companion live baseline. Existing local work was preserved.

## Identify the build before applying advice

HomeCompute pins dime source `0c391a3e74b6a775cfe248691ca7fd855b1876a5`,
parent image `vllm/vllm-openai:qwen38-flash-next@sha256:fc120ece0a388cc0aa1caad4a9f1cd92113484ab7ec2fd0efadd62585be05bf8`,
model `Saren/Qwen3.8-Flash-Next-W4A16-AutoRound-hybrid@8b82f0b7abe3d1150a7827d298c75e86267636ae`
and PLE table `Saren/Qwen3.8-Flash-Next-ple-table-fp8@50511b0a41aa1d34b8beb7e5d4bb06a0b650dc14`.
The dense-MTP g32 transform modifies drafter modules and links unchanged target
shards. Compare the local build report, protected runtime manifest and actual
container image ID; an upstream image ID does not identify the HomeCompute build.
See [canonical catalog](../../config/model-catalog.json),
[installer](../../scripts/setup-compute-flash-next.sh), and
[pinned upstream build provenance](https://raw.githubusercontent.com/dime-online/qwen3.8-Flash-DGX-UltraFast/0c391a3e74b6a775cfe248691ca7fd855b1876a5/docs/BUILD.md).

The upstream snapshot advertises vLLM `0.1.dev20073`, CUDA 13.0 and the V2 model
runner. The parent investigation independently read installed package metadata
`0.1.dev20073+g8e685d198` from live image
`sha256:b80718ed664f12514a24363894c61c5d36f9ea8ca294244a149ddb672df2dc14`;
the package direct URL identified a local wheel, not a source checkout. Its published peak throughput uses copy-heavy output with a shared cached
prefix; it is not a promised household workload rate. This is a patched preview
image, so current stable/latest vLLM documentation alone cannot establish knob,
metric or profiler support. Capture installed package version and source hashes
from the live image before interpreting behavior.
[Upstream snapshot](https://raw.githubusercontent.com/dime-online/qwen3.8-Flash-DGX-UltraFast/0c391a3e74b6a775cfe248691ca7fd855b1876a5/README.md).

## Knobs established by this exact recipe

The launcher exposes context, maximum sequences, KV bytes, prefix caching and
MTP depth. It fixes chunked prefill and 8,192 batched tokens. HomeCompute selects
9g KV rather than upstream's 16g, `safetensors` loading rather than
`fastsafetensors`, and PLE prewarm off. The installer records a prior DMA allocator
failure with the alternative loader; do not reverse that workaround as a casual
startup optimization. Its validation explicitly rejects changes to the
262,144/8/9g/MTP-3 tuple. An experiment must update the canonical declaration,
installer validation, source hash expectations and relevant tests together;
a live environment edit is not a supported variant.
[Exact launcher](https://raw.githubusercontent.com/dime-online/qwen3.8-Flash-DGX-UltraFast/0c391a3e74b6a775cfe248691ca7fd855b1876a5/recipe/config/v16b/serve.sh),
[HomeCompute environment](../../config/compute-node.env.example),
[installer](../../scripts/setup-compute-flash-next.sh),
[lifecycle tests](../../tests/flash-next-lifecycle-test.py).

`GPU_MEM=0.01` must not be read as a one-percent cap on total residency.
The parent read the installed `v1/worker/gpu_worker.py` lines 604–625:
when explicit KV bytes are set, the worker profiles to compile the configured
batch shape, skips automatic memory sizing, states that GPU utilization is not
respected, and returns the explicit allocation after multimodal IPC reservation.
This confirms the interpretation in this exact image; current documentation is
only supporting background. Lowering configured context while retaining 9g KV does not
by itself establish a 9g allocation reduction; measure recurrent-state, graph and
workspace effects separately. The source-supported experiment is to vary one of
context, sequences, batched tokens or explicit KV pool at a time, preserving the
other settings and validating the longest admitted workload.
[Current vLLM cache semantics, background only](https://docs.vllm.ai/en/latest/api/vllm/config/cache/).

Prefix hits in this hybrid model reconcile attention and recurrent-state cache
groups. A prefix-caching flag alone proves neither reuse nor correctness. The
pinned image includes diagnostics for reconciliation, publication, eviction and
prefill chunk boundaries. They are disabled by default; enabling them changes
logging behavior and is outside the read-only investigation. Prefer existing
aggregate metrics and per-request cached-input counts first.
[Pinned cache diagnostic implementation](https://raw.githubusercontent.com/dime-online/qwen3.8-Flash-DGX-UltraFast/0c391a3e74b6a775cfe248691ca7fd855b1876a5/recipe/build/image/src/patch_hit_debug.py).

## Reasoning and measurements

The pinned model's public `chat_template.jinja` defaults unspecified thinking to
on and effort to `xhigh`; thinking-on accepts `xhigh`, `medium` and `low`.
`enable_thinking=false` emits a closed empty thinking block. Therefore a working
OpenAI-level `reasoning_effort=none` requires compatibility mapping before
rendering; it is not the same as passing `none` directly as a thinking-on Jinja
effort. The parent investigation read the installed image
`0.1.dev20073+g8e685d198`: `entrypoints/openai/chat_completion/protocol.py`
lines 584–588 and `entrypoints/openai/responses/protocol.py` lines 335–339
set `enable_thinking` to `reasoning_effort != "none"` when effort is supplied
and the user has not explicitly set `enable_thinking` in
`chat_template_kwargs`. This verifies server-side mapping and precedence in the
installed image; gateway preservation and actual client payload still require
end-to-end checks. Score visible output, tool calls and hidden-token counts.
The public template was read directly over HTTPS; the browser reader could not render this URL.
[Pinned template](https://huggingface.co/Saren/Qwen3.8-Flash-Next-W4A16-AutoRound-hybrid/raw/8b82f0b7abe3d1150a7827d298c75e86267636ae/chat_template.jinja).

The exact upstream benchmark names draft, draft-token, accepted-token,
accepted-per-position, prefix-hit/query and running/waiting metrics. Confirm
which actually exist in the running image. For an uncontaminated interval,
accepted/draft-tokens measures acceptance; `1 + accepted/drafts` estimates emitted
tokens per speculative step. Neither metric alone proves a net speedup: compare
matched output correctness and wall/decode latency against an approved MTP-off
variant later. Foreign production traffic invalidates per-fixture attribution of
global deltas. The upstream runner's TTFT counts either reasoning or content;
record first visible answer separately for assistant experience.
[Pinned benchmark implementation](https://raw.githubusercontent.com/dime-online/qwen3.8-Flash-DGX-UltraFast/0c391a3e74b6a775cfe248691ca7fd855b1876a5/recipe/benchmarks/bench_copy_streams.py).

MTP vocabulary is English/code weighted. Danish acceptance must be measured;
upstream has no Danish acceptance guarantee. Keep block rejection and
probabilistic draft sampling together, preserve the checkpoint transform and
compare correctness as well as speed. The recipe distinguishes shared-memory
allotment from KV pool and treats changes as new variants requiring evaluation.
[Pinned configuration reference](https://raw.githubusercontent.com/dime-online/qwen3.8-Flash-DGX-UltraFast/0c391a3e74b6a775cfe248691ca7fd855b1876a5/docs/CONFIGURATION.md).

Do not call a modern profiler endpoint based on current docs. This build's
profiler patch explicitly says it lacks `/start_profile` support and requires a
startup environment switch plus trigger file, capturing 24 engine steps. That
would add overhead and require separate authorization; no profiler was enabled.
[Pinned profiler implementation](https://raw.githubusercontent.com/dime-online/qwen3.8-Flash-DGX-UltraFast/0c391a3e74b6a775cfe248691ca7fd855b1876a5/recipe/build/image/src/patch_step_profile.py).

## Unified memory and thermal interpretation

NVIDIA documents GB10 CPU/GPU shared DRAM and expected unsupported framebuffer
usage fields in `nvidia-smi`. CUDA free-memory reports also omit reclaimable
memory available through CPU paging. Use host memory availability, swap counter
*deltas*, PSI, cgroup memory and process PSS together; do not sum Docker memory,
GPU process memory and host used memory as independent pools. Swap occupancy
alone does not show current swapping. Never flush caches or disable swap to
manufacture a cold sample on production.
[NVIDIA UMA known issues](https://docs.nvidia.com/dgx/dgx-spark/known-issues.html).

Linux PSI quantifies task stalls; `some` indicates at least some stalled tasks,
whereas `full` represents all non-idle tasks stalled together. Read both memory
and I/O PSI with swap deltas and latency, recording unsupported fields explicitly.
[Linux PSI documentation](https://docs.kernel.org/accounting/psi.html).

Record supported temperature, power, clocks and throttle reasons during naturally
occurring load. The NVIDIA hardware guide describes cooling and the supplied
power adapter requirement; it supplies no basis for changing clocks, power limits
or firmware in this task. Hardware documentation for DGX Spark does not replace
the actual ASUS appliance's vendor specifications.
[NVIDIA hardware guide](https://docs.nvidia.com/dgx/dgx-spark/hardware.html).

## Validation and remaining uncertainty

This is a source review, not runtime qualification. Browser retrieval succeeded
for pinned upstream raw source and NVIDIA/Linux documentation; model template
retrieval required direct HTTPS. A public `vllm-project/vllm` raw-source lookup
using metadata commit
`8e685d198` returned HTTP 404 for cache and chat protocol files, so the metadata
suffix did not provide independently retrievable source for those semantics.
The installed source inspections above verify reasoning mapping and explicit
KV allocation semantics despite that public-source gap. The parent recorded:

| Installed file | SHA-256 |
| --- | --- |
| `v1/worker/gpu_worker.py` | `923767cea120a027ca36683bd48b7659eadbfa4cbd4acbcf52a80cee0c0a0ec4` |
| `entrypoints/openai/chat_completion/protocol.py` | `cb756e3d18e9061a2b306f305e10bd71d43f01ad1b236d0e9cbbb8756cd504dc` |
| `config/cache.py` | `b2ef271ee0c8849936c0cb97aee3a015117f19c337298e8f0008f08aca81f75d` |

The canonical protected `/srv/gb10-ai/manifests/flash-next/ultrafast/runtime.env`
was permission denied and noninteractive sudo was unavailable. Consequently
model/build provenance is not claimed matched merely from argv, directory names
or image identity. Live metrics, thermal telemetry and the outstanding protected
manifest check belong to the live baseline. The parent also
read `/model/config.json`: internal identifiers `model_type=qwen4_exp` and
`architectures=[Qwen4ExpForConditionalGeneration]` are runtime implementation
names, not evidence for substituting a different public model family. No
production synchronization, inference quality or recovery claim follows from
these sources. `git diff --check` and
repository-relative link existence checks passed for this new file.
