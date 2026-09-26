# Hviske Danish STT on home-spark

This is an opt-in, isolated Danish speech-to-text profile. It does not replace
the CPU Faster Whisper fallback on `home-core`, alter the existing modality
scaffold, or enable a Home Assistant route by itself.

The runtime tuple is intentionally separate:

- `syvai/hviske-v5.3` at commit
  `5d1a09822018702dc51d763e3a867b62d26b3501`;
- NVIDIA vLLM 26.04 ARM64 at image digest
  `sha256:ebd2b86dd262729d44df230961bd42d623b39490adef4bf16e7f11dda5a6098d`;
- vLLM 0.19.0 and Transformers 4.57.6, matching the publisher-tested service
  path;
- Danish, deterministic `/v1/audio/transcriptions` requests with
  `language=da` and `temperature=0`.

The model publisher documents the vLLM 0.19 launch and OpenAI transcription
endpoint in the [Hviske v5.3 model card](https://huggingface.co/syvai/hviske-v5.3).
NVIDIA documents the packaged versions in the
[26.04 release notes](https://docs.nvidia.com/deeplearning/frameworks/vllm-release-notes/rel-26-04.html).

## Network boundary

`hviske-primary` has no host port. The standard-library adapter is its only
client and publishes Wyoming on `10301/tcp`. During preparation it binds
`127.0.0.1:10301`; promotion changes that exact bind to
`10.77.10.10:10301`. The shared compute firewall must already allow only
`10.77.10.2/32` to that destination before
`HVISKE_PRIVATE_INGRESS_CONFIRMED=true` may be set.

The adapter advertises one ASR program and one Danish model, requires external
VAD, accepts at most 60 seconds or 10 MB of PCM, rejects non-Danish/model
requests, preserves the incoming PCM format in a bounded WAV, and authenticates
to vLLM with the existing file-mounted API key. It admits at most four active
Wyoming connections, writes neither audio nor
transcripts to disk and vLLM request/access logging is disabled.

## License gate

Hviske v5.3 is CC BY-NC 4.0. The default
`HVISKE_LICENSE_DECISION=review-required` permits validation and artifact
preparation but blocks service activation. Set it to
`personal-noncommercial` only after confirming the deployment is personal and
noncommercial. Employer, work, or commercial use requires documented SYVAI
approval and the value `syvai-commercial-license-approved`.

## Prepare without routing

On `home-spark`:

```bash
sudo install -m 0640 -o root -g gb10-ai \
  config/hviske-stt.env.example /etc/gb10-ai/hviske.env
sudo editor /etc/gb10-ai/hviske.env

sudo ./scripts/setup-compute-hviske-stt.sh validate
sudo ./scripts/setup-compute-hviske-stt.sh preflight
sudo ./scripts/setup-compute-hviske-stt.sh prepare
```

`prepare` downloads the exact public revision through the bounded cache helper,
then atomically accepts an integrity manifest. It does not start either service.

## Stage and qualify

After the license decision is reviewed, start on loopback first:

```bash
sudo ./scripts/setup-compute-hviske-stt.sh up
sudo ./scripts/setup-compute-hviske-stt.sh smoke
sudo ./scripts/setup-compute-hviske-stt.sh status
```

Run Danish clean/noisy/name/address/device fixtures and representative mixed
GPU load before promotion. `smoke` proves Wyoming Describe plus an actual
bounded transcription request; it is not accuracy or production evidence.

After the physical private link and exact firewall rule pass, change only:

```dotenv
GB10_BIND_ADDRESS=10.77.10.10
GATEWAY_CIDR=10.77.10.2/32
HVISKE_PRIVATE_INGRESS_CONFIRMED=true
```

Then rerun `validate`, `up`, and `smoke`. On `home-core`, start the guarded
`hviske-wyoming-proxy` only after its upstream health check passes. Home
Assistant registers the LAN relay at `192.168.30.122:10301`; it must not route
directly to the compute-only `10.77.10.10` address. Select the advertised Dansk
model first in a canary Assist pipeline and retain the existing Faster Whisper
entity as rollback. Promotion remains blocked until the link, both firewalls,
relay, model qualification, and license gates all have evidence.

To stop only this profile while retaining artifacts:

```bash
sudo ./scripts/setup-compute-hviske-stt.sh down
```
