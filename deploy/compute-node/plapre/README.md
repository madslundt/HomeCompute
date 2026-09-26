# Opt-in Plapre Nano v2 TTS on home-spark

This isolated compute-side project adds Danish Plapre TTS without changing the
default compute stack or replacing the existing Piper Wyoming service. It is
not started by `deploy/compute-node/compose.yaml` and is not production-routed
by any setup command in this directory.

## Immutable runtime

The derived ARM64 image starts from NVIDIA vLLM 26.02 at digest
`sha256:604e5b052d1ce1b87952c72bf95cc637192c0051fc15a32886dff20bffd5c514`
(vLLM 0.15.1). The Dockerfile checks out the exact Plapre and Kanade commits,
downloads the three exact model revisions, verifies the four large-file
SHA-256 values, builds torchaudio from the pinned upstream `v2.11.0` source
against NVIDIA's exact Torch 2.11 alpha ABI, and preloads WavLM during the image
build. The public PyPI torchaudio wheel is intentionally not installed because
its stable Torch ABI is incompatible with NVIDIA 26.02. Runtime Hub access is
disabled. Checked-in patches require local Kanade and HiFT paths instead of
falling back to mutable Hub branches.

On the GB10, NVIDIA Torch 26.02 cannot select a CUDA engine for Kanade's fp32
`ConvTranspose1d`. The project therefore uses a bounded hybrid decoder: Plapre
token generation and Kanade attention remain on the GPU, the single unsupported
Kanade upsampler crosses to CPU, and HiFT vocoding runs on CPU with BLAS/OpenMP
threads capped. This avoids changing or downgrading NVIDIA's Torch build and
reduces decoder GPU residency. The deployed tuple was verified over Wyoming as
24 kHz, 16-bit, mono PCM; keep this workaround enabled until an NVIDIA base
image passes the same synthesis test entirely on GPU.

The complete rationale, primary sources, licenses, and qualification limits are
in [the dated deployment research](../../../docs/research/plapre-nano-v2-gb10-home-assistant-deployment-2026-09-26.md).

## Network boundary

`plapre-primary` listens only on an internal Docker network at port 8004. It has
no host port and no runtime egress. `plapre-wyoming` is the only application
client of that unauthenticated API. A byte relay publishes only Wyoming
`10201/tcp`, initially on loopback and later on `10.77.10.10` after the exact
private-link/firewall rule is independently installed and confirmed.

The adapter advertises only `danish-default`, maps it internally to packaged
speaker `tor`, limits text and response size, and verifies Plapre's 24 kHz,
16-bit, mono PCM headers. It neither accepts reference audio nor exposes voice
cloning or internal speaker IDs. The household profile applies a fixed 1.25x
pitch-preserving tempo adjustment before emitting the same PCM format.

## Voice and fallback gates

The Plapre checkpoint is CC BY 4.0 and is retained with its model card in the
derived image. Upstream does not document identity or consent provenance for
the packaged speakers. The example configuration therefore sets
`PLAPRE_VOICE_APPROVAL=review-required`; `up` refuses to start until the choice
is documented and changed to `approved-household-use`.

The current Piper service at Wyoming port 10200 remains installed and unchanged.
Keep Piper selected in the production Home Assistant Assist pipeline until the
Plapre canary passes Danish listening, latency, repetition, mixed Qwen load, and
OOM-recovery qualification. This compute adapter does not proxy to Piper:
fallback remains an explicit Home Assistant entity/pipeline rollback and does
not widen access across the private link.

## Build without activation

On `home-spark`, install a root-owned copy and leave the review gates unchanged:

```bash
sudo install -m 0640 -o root -g gb10-ai \
  config/plapre-tts.env.example /etc/gb10-ai/plapre-tts.env
sudo ./scripts/setup-compute-plapre.sh validate
sudo ./scripts/setup-compute-plapre.sh build
```

`build` downloads public artifacts into the derived image and starts nothing.
After the voice decision, private ingress, and local qualification plan are
recorded, update the two approval fields and use `up` followed by `smoke`. No
command here modifies the host firewall, Home Assistant YAML, or Piper.

## Home Assistant canary

The compute private address is not a LAN endpoint. Home Assistant should reach
the guarded home-core relay created by the HA automation configuration, not
`10.77.10.10` directly. Register/select the resulting Wyoming TTS entity only
in a canary Assist pipeline. Preserve the current Piper entity as the default
and rollback until Plapre is promoted.
