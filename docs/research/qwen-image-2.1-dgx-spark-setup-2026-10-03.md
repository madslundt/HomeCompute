# Qwen-Image-2.1 on DGX Spark: runtime and lifecycle recommendation

Verified 2026-10-03 against official model/runtime documentation and public
DGX Spark user reports. This note recommends an integration path for the
single-GB10 `home-spark`; it does not replace the deployment ADRs or a local
qualification run.

## Recommendation

Use **SGLang Diffusion as a separately managed image lane**, with only one
GPU-serving model process active at a time. Boot `Qwen3.8-Flash-Next` as the
default text model. Load Qwen-Image-2.1 only on explicit image-mode selection
or an image request, and after one hour with no image request or in-flight
generation, stop the image service and start Flash-Next again. The lifecycle
controller should own the lease/timer and readiness checks: the SGLang
Diffusion image lane does not provide the LLM `--sleep-on-idle` option.

This recommendation best fits the requested single-GPU model switching. It is
supported by a Spark-specific official cookbook and by a working, community
maintained Spark lane with load/switch/unload controls. Keep batch size one and
all image components resident for interactive generation. Start with BF16,
Torch SDPA selected by the runtime, eager execution, no CPU offload, and
1024×1024 at 40 steps. A 20-step draft mode is a reasonable later option.

## Evidence on the Spark

| Path | Spark evidence | Operational fit |
| --- | --- | --- |
| **SGLang Diffusion** | Official cookbook: 1024², 40 steps, CFG 1: 35.36 s generation / 42.23 s edit. Community lane: 38.2 s generation and 34.8 GB reported peak; 2048²/40: 190.9 s and 44.7 GB. | Best current recommendation. One GPU and resident weights; image-only service can be switched against text lane. Upstream cookbook says use its source installation for this model; no published Docker image is verified there. |
| **Diffusers** | Independent NVIDIA Forum user: BF16, 1024²/40 in 54.1 s at 31.6 GB; 2048²/40 in 4m16s at 33.7 GB; model startup about 3 min. | Proven fallback and useful correctness baseline, but slower and its standalone pipeline needs a serving wrapper and lifecycle integration. |
| **ComfyUI** | Independent Spark test: ComfyUI 0.36.0, official INT8 ConvRot template, 1024²/25 in about 20 s and 2048²/25 in 128 s; test suite 23/23. | Strong interactive UI option, but its tested setup is a Comfy workflow/UI lane rather than a simple OpenAI-compatible model API. |
| **vLLM-Omni** | Qwen's page and the vLLM recipe describe the feature set, but the Qwen-Image-2.1 support PR remains open. The recipe reports 4.5 s on one GB300; this is not a Spark result. | Re-evaluate after merge, release, and a real GB10 comparison. Avoid making the current experimental branch the production load/unload path. |

The official SGLang cookbook recommends one GB10 GPU on Linux ARM64/CUDA 13,
resident BF16/FP32 components, Torch SDPA, eager execution, full-image VAE
decode, and batch size one. It says the 128 GB unified memory is sufficient
without CPU offload for verified single-image 1024² use. The measured 35.36 s
generation and 42.23 s edit results were captured 2026-09-20 after warmup;
startup is excluded. A second Spark owner's implementation reports 38.2 s at
1024²/40 and 34.8 GB peak, and about 60–72 s warm-cache startup. These timings
are compatible but come from distinct runs and should not be combined into one
benchmark claim.

The second owner also reports that the 31 GB image weights do not fit beside
the serving LLM in their measured lane, and gates all starts so the image and
text engines cannot overlap. This matches `home-spark`'s one-model-at-a-time
constraint. SGLang Diffusion currently has no native idle-sleep switch, so the
home-spark manager must stop the process and start the configured default text
service itself.

## Runtime and quality details

- Keep CPU offload disabled for the Spark's tested 1024² lane. In the
  independent Diffusers run, enabling model CPU offload raised a one-reference
  edit from 62.5 seconds to about 4 minutes 20 seconds.
- Keep VAE tiling off when using the full-image Spark recipe. A Diffusers user
  traced stable pink/purple vertical seams to tiled VAE decoding; disabling
  tiling removed the measured seams. Untiled 2048² decoding used about 28 GB
  extra memory in that user's run, which fit their 120 GB unified-memory Spark.
- Limit the first shipped path to one generation at a time. SGLang's cookbook
  advises interactive users to leave batching off and request one output; it
  warns batching can increase individual latency and alter output pixels.
- Pin the SGLang source revision and model revision, and qualify cold startup,
  health/readiness, image generation/editing, process stop, memory release, and
  restart to Flash-Next on the actual host. The cookbook states that this
  integration currently uses the Python/source command and has no verified
  published Docker image. A community installation documents installing the
  released aarch64 wheel first for its kernel package, then applying the
  pinned source tree without dependency resolution; follow the current
  upstream cookbook and validate that exact install sequence before adopting
  it.

## Lifecycle shape for `home-spark`

1. On boot, start Flash-Next and expose the existing stable text API only when
   readiness passes.
2. On image-mode selection/request, serialize the transition: reject or queue
   work while the current model drains, stop Flash-Next, start the pinned image
   service, and wait for image readiness before dispatching the request.
3. Track image work as a lease. Reset the one-hour idle deadline on each
   accepted image request; do not expire while a request is queued or running.
4. At expiry, stop the image process, confirm it exited and released its
   serving slot, start Flash-Next, and restore the text API after readiness.
5. Keep lifecycle state explicit (`starting`, `ready`, `busy`, `stopping`,
   `failed`) so a restart or failed model load cannot report the wrong active
   model. Expose the selected model and transition status to the UI/API.

Treat this sequence as a design recommendation derived from Spark measurements
and the community lane's one-engine gate; its transitions still require
qualification in the home-spark manager.

## Why not vLLM-Omni yet?

vLLM-Omni is technically promising: its recipe describes prefix-KV reuse,
CUDA-graph decode, optional FP8, and image API endpoints. However, its
Qwen-Image-2.1 PR is still open on the verification date, and the supplied
recipe's best end-to-end number is from a GB300, not GB10. The open PR leaves
Spark-specific startup and correctness unproven. An NVIDIA Spark forum user
who shared a build from that PR says their first attempt failed on Spark and
needed a community Dockerfile patch; another thread has no vLLM-Omni speed
result. A separate open issue reports corrupted output under TP=2; that issue
uses two consumer GPUs, so it does not establish a single-GB10 defect, but it
is another reason not to use distributed parallelism for this one-GPU setup.

## Sources

- Qwen's [Qwen-Image-2.1 model repository](https://github.com/QwenLM/Qwen-Image-2.1) documents Diffusers, ComfyUI, vLLM-Omni and SGLang integration, capabilities, supported sizes, and the Qwen Research License. It recommends Diffusers as the community pipeline and links the serving runtimes.
- SGLang's [Qwen-Image 2.1 cookbook](https://docs.sglang.io/cookbook/diffusion/Qwen-Image/Qwen-Image-2.1) contains Spark settings and its 2026-09-20 measurement methodology/results, and marks unverified hardware/topology combinations.
- A Spark owner's [tested SGLang image lane](https://github.com/hasso5703/dgx-spark-qwen38/blob/main/docs/image-lane.md) records settings, measured time/memory/startup, pinned source setup, engine exclusivity, and lifecycle behavior.
- An independent Spark user report on the [NVIDIA Developer Forum](https://forums.developer.nvidia.com/t/qwen-image-2-1-on-dgx-spark-54-s-per-1024-image-40-steps-31-6-gb-and-a-fix-for-the-pink-vertical-line/384774) records Diffusers settings and repeated latency/memory measurements, offload comparisons, and the VAE tiling artifact investigation. The linked [reproduction repository](https://github.com/MindrLabs/image-generation-qwen-image-2.1) contains the code and outputs.
- A Spark test of ComfyUI is documented in [Qwen-Image-2.1 on one Spark: day-0 Comfy](https://www.smfclearinghouse.com/blog/2026-09-20-qwen-image-21-one-spark/), including a 23-case suite, exact software/weights, and raw test artifact links.
- The current [vLLM-Omni Qwen-Image-2.1 recipe](https://recipes.vllm.ai/Qwen/Qwen-Image-2.1) gives a GB300 result and explicitly describes the support's release/merge status. The underlying [support PR #7759](https://github.com/vllm-project/vllm-omni/pull/7759) is open and lists uncompleted hardware and serving verification; the open [TP output issue #8135](https://github.com/vllm-project/vllm-omni/issues/8135) describes a two-consumer-GPU reproduction.

## Recommendation boundary

The best evidenced current fit is SGLang Diffusion in a mutually exclusive
lane. The evidence does not establish final production readiness for this
repository: the SGLang integration is source-based, measurements differ by
setup, and lifecycle behavior must be tested against the actual home-spark
service manager. Run a short GB10 acceptance matrix before pinning it as the
default image runtime. Review Qwen's [license](https://github.com/QwenLM/Qwen-Image-2.1#license-agreement)
before uses outside its research-license terms.
