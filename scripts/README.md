# Setup scripts

The repository retains a privileged setup helper only for the vendor-managed
compute appliance. `home-core` is configured with `nixos-rebuild`.

| Script | Target | Mutating commands |
| --- | --- | --- |
| `setup-compute-node.sh` | NVIDIA GB10 or DGX Spark-class appliance | `init`, `firewall`, `install`, `rollback`, `down` |
| `setup-compute-modalities.sh` | Staged embedding, rendered-page vision, STT, OpenAI TTS, and Wyoming TTS on `home-spark` | `prepare`, `install`, `up`, `down` |
| `setup-home-core-piper.sh` | Pinned Danish MOSS ONNX with automatic Piper fallback on CPU-only `home-core` | `prepare`, `up`, `down`, `status` |
| `setup-home-core-stt.sh` | Pinned Wyoming Faster Whisper fallback on CPU-only `home-core` | `prepare`, `up`, `down`, `status` |
| `configure-compute-firewall.sh` | Invoked by compute setup and systemd | Exact persistent `DOCKER-USER` policy |
| `model-cache-integrity.py` | Invoked by compute setup and vLLM entrypoint | Accepted-cache manifest create/verify |
| `gb10_model_roster.py` | Offline configuration validation | Enforces the Qwen3.8 workhorse, two runtime profiles, exclusive Flash-Next cold swap, and exact four-model speech roster |
| `model_router_policy.py` | Offline model-router validation and decision tests | `validate`, `decide` |
| `codex_session.py` | Policy-aware Codex session entry point | Cloud default for committed `cloud_allowed` repositories; explicit whole-session GB10 Local mode |
| `initialize-compute-secrets.py` | Invoked by compute setup | Symlink-safe exclusive secret initialization |

The compute scripts default to safe, staged operation. Run `help`, `validate`,
and `preflight` first, and read the matching node plan before a mutating
command. They refuse unresolved placeholders and avoid deleting existing
models, caches, secrets, previous release records, or the text runtime.

```bash
./scripts/setup-compute-node.sh help
./scripts/setup-compute-modalities.sh help
nix flake check
nixos-rebuild build --flake .#home-core
```

The script must run from an intact repository checkout because it resolves
templates relative to their own location. Production configuration lives under
`/etc`, and runtime/model data lives outside the repository.

## Legacy modality scaffold

`setup-compute-modalities.sh` still represents the older Whisper/Piper and
optional embedding/vision qualification scaffold. Those checkpoints are not in
the final roster and these commands must not be used to install the new speech
stack. They remain only until the four selected speech runtimes have immutable
images and equivalent lifecycle checks.

After `setup-compute-node.sh init` has established the service identity,
directories, and API-key file, validate and stage the pinned, public artifacts:

```bash
./scripts/setup-compute-modalities.sh validate
./scripts/setup-compute-modalities.sh preflight
sudo ./scripts/setup-compute-modalities.sh prepare
sudo ./scripts/setup-compute-modalities.sh install --modality stt
./scripts/setup-compute-modalities.sh status --modality stt
./scripts/setup-compute-modalities.sh smoke --modality stt
```

`prepare` checks exact-revision Hugging Face metadata against reviewed
per-tuple byte/file ceilings, monitors the child downloads against the absolute
cache and free-space budgets, then creates or re-verifies immutable accepted
manifests for embedding, vision, and STT.
`install` requires `--modality embedding|vision|stt|tts`, pulls only
digest-pinned runtime images, installs the cache verifier
and TTS adapter as root-owned runtime programs, starts and exercises only the
selected profile, and records the program and artifact digests in a secret-free
staged release. Loopback is the default and requires no routed firewall rule.
After changing to the qualified private address, `install` reapplies and verifies
the exact source-restricted firewall. `up`, `logs`, and `down` are scoped to the
selected modality services; they do not recreate or remove `text-primary`. Use
`--modality all` only for an intentional mixed-load qualification run.

The HTTP listeners share the compute API key. Wyoming has no application
authentication and must remain on loopback until the exact private bind and
source-restricted firewall policy are deliberately enabled. Never put API keys,
speech, images, documents, or model responses in configuration or logs. Vision
accepts inline rendered image pages through `/v1/chat/completions`; native PDF
input and remote media URLs are intentionally unsupported.

These services remain staged, not production-qualified, until the release has
passed live `home-spark` GPU/memory, Danish quality, latency, mixed-load, and
recovery checks.

Repository validation:

```bash
./scripts/validate-repository.sh
```

Validate the selected model roster independently:

```bash
python3 scripts/gb10_model_roster.py \
  --roster config/gb10-model-roster.json
```

The offline [Danish TTS qualification](../docs/tts-qualification.md) qualifies
Plapre Nano v2 on the GB10 against the independent Piper fallback. Its harness
creates blinded listening packets and enforces warm p95 first audio at 750 ms
and warm p95 RTF at 0.5. The plan also requires ASR verification, at most one
resynthesis, and Piper recovery behavior. It does not install models.

`speech_routing_policy.py` validates and exercises the inactive language route
contract in `config/speech-routing-policy.json`; it never contacts a model.

The router policy records Qwen3.8-27B as the selected primary but remains
disabled and unbound until its exact runtime tuple passes live qualification. Validate it
without contacting LiteLLM or `home-spark`:

```bash
python3 scripts/model_router_policy.py validate \
  --policy config/model-router-policy.json
```

The checked-in contract keeps model activation and load-on-demand disabled. It
resolves the six task aliases without invoking a classifier, limits exact model
selection to the operator policy, and records `auto` proposals while serving
the active default. It is not yet wired into the live gateway; that integration
waits for Responses/stream lifecycle and lease tests.

This checks Bash syntax and ShellCheck, the non-executing configuration loader,
automation JSON, YAML when Ruby is installed, Compose rendering (including the
artifact-fetch profile), control-plane isolation policy, and D2 rendering when
D2 is installed.

## Published home-core deployments

`deploy-home-core.sh FULL_COMMIT_SHA` runs on home-core with sudo. It deploys a
clean GitHub commit, rebuilds NixOS, and applies the gateway, n8n, Homepage,
and prepared Piper projects. See [Git deployment](../docs/git-deployment.md)
for prerequisites and rollback limits. The books importer remains staged.
