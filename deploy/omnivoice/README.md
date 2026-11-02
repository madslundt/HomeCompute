# OmniVoice TTS replacement

The owner selected OmniVoice to replace Plapre for Danish-first household
speech and English. This independent project leaves STT and text runtimes
unchanged. Weights are CC-BY-NC; this configuration is for noncommercial
household use. The code is pinned to a full revision, artifacts are downloaded
and checksum-verified at build time, and inference is offline.

## Placement measurement

Run from a reviewed commit published on `origin/master`:

```bash
docker build -f deploy/omnivoice/Dockerfile.cpu -t homecompute/omnivoice:cpu-REVISION .
docker run --rm --network none --read-only --cap-drop ALL \
  --security-opt no-new-privileges:true --cpus 4 --memory 8g --memory-swap 8g \
  --pids-limit 256 --tmpfs /tmp:rw,noexec,nosuid,nodev,size=256m \
  homecompute/omnivoice:cpu-REVISION --benchmark
```

Replace `REVISION` with the selected full commit. Use `sudo -n docker` on
home-core. The measurement synthesizes only fixed synthetic Danish/English
phrases in memory, logs timing and memory metadata, and retains no audio.
It uses a deterministically seeded synthetic voice; a reference recording and
an extra Whisper model are not required. These measurements select placement,
not native-listener pronunciation qualification or service readiness.

The existing MOSS/Piper CPU service is independent. Retain it during migration.
No general host release is needed to build this isolated image.

On Spark, `python3 scripts/setup-omnivoice.py benchmark-gpu --revision FULL_SHA`
builds the pinned NVIDIA ARM64 image and runs the same fixed in-memory test.
It installs torchaudio from the exact source revision against NVIDIA's Torch
ABI; a generic PyPI torchaudio wheel would replace the vendor Torch tuple.
The engine accepts only fixed Danish/English speakers and no reference paths.

The initial home-core test at `dede6633b4716d8e507ac53990ed717da9447473`
measured 15.659s for 1.880s of Danish audio and 23.791s for 2.690s, with peak
RSS about 2.5GiB. English exposed an optional-normalizer dependency, fixed by
using the existing Danish num2words normalizer only for Danish. These first
samples do not establish warm p95, but reject CPU placement for the intended
interactive latency. GPU measurement remains necessary.

## Retire Plapre

From the selected clean published checkout on each host, run:

```bash
# First, on home-core, preserve voice availability in both HA pipelines:
sudo -n python3 scripts/setup-omnivoice.py ha-fallback --revision FULL_SHA
# Then retire only Plapre's containers on each host:
python3 scripts/setup-omnivoice.py retire-plapre --revision FULL_SHA
```

Use `sudo -n python3` on home-core. The Spark operator already has Docker
access. This command inspects the exact Compose project/service/name tuple
before any mutation, records image/container identities in private operator
state, disables restart, stops, and removes only those containers. It is
idempotent after successful retirement. It never prunes volumes or images.
Do not run general speech deployment during the replacement: old published
releases still contain Plapre. Consumer routing is migrated by the replacement
lifecycle before normal releases resume.

The retained previous release and image can recreate Plapre only through its
documented lifecycle, with existing external environment files. That rollback
is explicit and is not performed automatically after the owner requested
Plapre removal. MOSS/Piper remains independent.
