# Setup scripts

The workstation `homecompute` CLI provides status, updates, model and service
inventory, diagnostics, and explicit SHA-based host deployment. See
[`docs/homecompute-operations.md`](../docs/homecompute-operations.md).

The repository retains a privileged setup helper only for the vendor-managed
compute appliance. `home-core` is configured with `nixos-rebuild`.

| Script | Target | Mutating commands |
| --- | --- | --- |
| `setup-compute-node.sh` | NVIDIA GB10 or DGX Spark-class appliance | `init`, `firewall`, `install`, `rollback`, `down` |
| `setup-compute-automation-moe.sh` | Opt-in Qwen3.6 MoE candidate on `home-spark` | `prepare`, `install`, `activate`, `deactivate` |
| `setup-compute-home-assistant-model.sh` | Isolated Gemma 4 E4B fast Home Assistant fallback | `prepare`, `install`, `up`, `smoke`, `down` |
| `setup-compute-plapre.sh` | Isolated Plapre Nano v2 Danish TTS on `home-spark` | `build`, `up`, `down` |
| `setup-compute-hviske-stt.sh` | Isolated Hviske v5.3 Danish STT on `home-spark` | `prepare`, `install`, `up`, `down` |
| `setup-compute-modalities.sh` | Staged embedding, rendered-page vision, STT, OpenAI TTS, and Wyoming TTS on `home-spark` | `prepare`, `install`, `up`, `down` |
| `setup-home-core-piper.sh` | Pinned Danish MOSS ONNX with automatic Piper fallback on CPU-only `home-core` | `prepare`, `up`, `down`, `status` |
| `setup-home-core-stt.sh` | Pinned Wyoming Faster Whisper fallback on CPU-only `home-core` | `prepare`, `up`, `down`, `status` |
| `setup-home-core-automation-backup.sh` | On-demand Qwen3.6 Q4 CPU standby for planned Spark swaps | `prepare`, `maintenance-start`, `smoke`, `maintenance-stop`, `status` |
| `setup-hermes-guest.sh` | Synthetic `agent-owner` Hermes canary inside the isolated Ubuntu agents guest | `validate`, `preflight`, `install`, `onboard-canary`, `health`, `snapshot`, `restore-verify` |
| `configure-compute-firewall.sh` | Invoked by compute setup and systemd | Exact persistent `DOCKER-USER` policy |
| `model-cache-integrity.py` | Invoked by compute setup and vLLM entrypoint | Accepted-cache manifest create/verify |
| `gb10_model_roster.py` | Offline hardware-roster validation | Immutable revisions, resolvable operating-profile references, runtime profile structure, and artifact bounds |
| `model_registry.py` | Canonical text artifact/deployment/route validation and deterministic LiteLLM generation | `validate`, `render`, `check` |
| `modelctl.py` | Show and validate compute releases against the model catalog | `show`, `validate` |
| `verify_client_model_access.py` | Live `/v1/models` visibility audit for supplied client keys | Exact expected aliases per environment-variable credential; never prints key values |
| `codex_session.py` | Policy-aware Codex session entry point | Cloud default for committed `cloud_allowed` repositories; explicit whole-session GB10 Local mode |
| `initialize-compute-secrets.py` | Invoked by compute setup | Symlink-safe exclusive secret initialization |

The compute scripts default to safe, staged operation. Run `help`, `validate`,
and `preflight` first, and read the matching node plan before a mutating
command. They refuse unresolved placeholders and avoid deleting existing
models, caches, secrets, previous release records, or the text runtime.

```bash
./scripts/setup-compute-node.sh help
./scripts/setup-compute-automation-moe.sh help
./scripts/setup-compute-plapre.sh help
./scripts/setup-compute-hviske-stt.sh help
./scripts/setup-compute-modalities.sh help
nix flake check
nixos-rebuild build --flake .#home-core
```

The script must run from an intact repository checkout because it resolves
templates relative to their own location. Production configuration lives under
`/etc`, and runtime/model data lives outside the repository.

## Opt-in n8n automation MoE

`setup-compute-automation-moe.sh install` downloads and accepts the pinned
Unsloth Qwen3.6-35B-A3B NVFP4 artifact but does not load it. `activate` stops
the normal Qwen3.8 service before starting the separate `automation-moe`
profile on port 8005; `deactivate` restores Qwen3.8. The baseline disables
thinking and MTP, uses the model-card `qwen3_coder` parser, and remains an
explicit qualification route. The ordinary `automation` alias does not move
until Danish, structured-output, real n8n, 64-tool, memory, and recovery tests
pass. Flash-Next is never started by this lifecycle.

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

Validate the hardware-scoped model roster and canonical capability registry:

```bash
python3 scripts/gb10_model_roster.py \
  --roster config/gb10-model-roster.json
python3 scripts/model_registry.py validate
python3 scripts/model_registry.py render --output /tmp/litellm-config-candidate.yaml
python3 scripts/model_registry.py check
```

The stdlib chat example accepts an alias with `--model`, but that selects only
the requested route; it does not grant the API key permission to use it. As of
2026-09-27, the three documented client keys are allow-listed only for
`automation-moe`. Requests to other aliases return HTTP 403 until an operator
provisions a separately scoped virtual key. Check authenticated `/v1/models`
to see the aliases available to a particular key. The helper keeps plain chat
as the default and accepts `--tools --tool-choice auto|required` to show a
function schema and surface returned `tool_calls` without executing them. The
guarded model smoke commands test runtime profiles directly; they do not prove
that a client key is authorized for those aliases. These checks do not publish
or invoke any n8n workflow.

The access audit reads keys from named environment variables and checks the
exact `/v1/models` alias set without displaying credentials. For a live
control-plane check, pass each scoped credential separately, for example:

```bash
python3 scripts/verify_client_model_access.py \
  --base-url https://ai.home.arpa \
  --ca-file ~/.config/homecompute/home-core-root.crt \
  --expect HOMECOMPUTE_SCRIPT_API_KEY=automation-moe
```

The offline [Danish TTS qualification](../docs/tts-qualification.md) qualifies
Plapre Nano v2 on the GB10 against the independent Piper fallback. Its harness
creates blinded listening packets and enforces warm p95 first audio at 750 ms
and warm p95 RTF at 0.5. The plan also requires ASR verification, at most one
resynthesis, and Piper recovery behavior. It does not install models.

`speech_routing_policy.py` validates and exercises the inactive language route
contract in `config/speech-routing-policy.json`; it never contacts a model.

The model registry validates artifact/runtime compatibility, deployment
references, local-only routes, qualification state, context requirements, and
timeout profiles. The renderer edits only LiteLLM's `model_list`; it preserves
key management, logging/privacy, database, retry, and router settings. The
old experimental `model-router-policy.json` has been retired because it was
not wired into LiteLLM and duplicated alias, model, and client-policy data.

`render` writes the checked-in LiteLLM config by default. Review its diff and
retain the prior config before any controlled deployment. It does not deploy
or reload LiteLLM, change virtual-key permissions, or open compute ports.

This checks Bash syntax and ShellCheck, the non-executing configuration loader,
automation JSON, YAML when Ruby is installed, Compose rendering (including the
artifact-fetch profile), control-plane isolation policy, and D2 rendering when
D2 is installed.

## Published home-core deployments

`deploy-home-core.sh FULL_COMMIT_SHA` runs on home-core with sudo. It deploys a
clean GitHub commit, rebuilds NixOS, and applies the gateway, n8n, Homepage,
and prepared Piper projects. See [Git deployment](../docs/git-deployment.md)
for prerequisites and rollback limits. The books importer remains staged.
