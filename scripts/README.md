# Setup scripts

The workstation `homecompute` CLI provides status, updates, model and service
inventory, diagnostics, and explicit SHA-based host deployment. See
[`docs/homecompute-operations.md`](../docs/homecompute-operations.md).

The repository retains a privileged setup helper only for the vendor-managed
compute appliance. `home-core` is configured with `nixos-rebuild`.

| Script | Target | Mutating commands |
| --- | --- | --- |
| `setup-compute-node.sh` | NVIDIA GB10 or DGX Spark-class appliance | `init`, `firewall`, `install`, `rollback`, `down` |
| `setup-compute-automation-moe.sh` | Current Qwen3.6 automation lifecycle and rollback | `prepare`, `install`, `activate`, `deactivate` |
| `setup-compute-flash-next.sh` | Shared exclusive lifecycle for Blazux quality baseline and dime UltraFast challenger | `validate`, `prepare`, `install`, `activate-canary`, `switch-profile`, `smoke`, `status`, `deactivate-canary` with `--profile quality|ultrafast` |
| `setup-compute-automation-nvidia.sh` | Historical NVIDIA Qwen3.6 qualification lifecycle | `validate`, `prepare`, `install`, `activate`, `smoke`, `deactivate` |
| `setup-compute-home-assistant-model.sh` | Historical Gemma 4 E4B Home Assistant service | `prepare`, `install`, `up`, `smoke`, `down` |
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
| `sparkrun-model-manager.py` | Fixed JSON interface for catalog-approved vLLM workloads on `home-spark` | `list`, `status`, `prepare`, `load`, `unload`, `replace` actions on stdin |
| `setup-compute-sparkrun-manager.sh` | Root-run forced-command SSH boundary for the sparkrun model manager | `validate`, `install` |
| `verify_client_model_access.py` | Live `/v1/models` visibility audit for supplied client keys | Exact expected aliases per environment-variable credential; never prints key values |
| `codex_session.py` | Policy-aware Codex session entry point | Cloud default for committed `cloud_allowed` repositories; explicit whole-session GB10 Local mode |
| `assistant-task.py` | Approved local task broker operator interface | Per-task execution approval, cancellation, private proposal export and digest-bound draft PR publication |
| `openclaw_tasks.py` | Trusted coding handoff and status feed | Fixed loopback broker submission/cancellation, durable public event cursor to existing communication outbox; no execution or publication approval |
| `verify-openclaw-runtime.py` | Isolated synthetic local-model probe | One native OpenClaw tool loop against a temporary pending-only broker; never invokes Codex or GitHub |
| `prepare-openclaw-nemoclaw.py` | Local private managed-config preparation | Preserves NemoClaw gateway/proxy ownership; never installs, uploads, provisions credentials or deploys |
| `setup-openclaw-browser.py` | Dedicated guest headless browser | Immutable image, private CDP token and bounded containers; refuses existing container replacement |
| `enable-openclaw-tools.py` | In-sandbox browser/CLI activation | Native schema dry-run by default; explicit apply backs up config and scopes exec to one helper; current activation blocked, see `docs/openclaw-tools.md` |
| `check-maintenance.py` | Daily host-local package and Docker registry reports | Fresh isolated signed APT indexes, deployed NixOS catalog revision and public manifest metadata; never apply updates |
| `setup-maintenance-monitor.py` | Install the dedicated metadata timer | Digest-bound dry-run/install/rollback; only the monitor is activated |
| `openclaw-observation-feed.py` | Update report admission | Existing fixed SSH reads, finite summary to private outbox, meaningful notifications through its n8n owner |
| `observe-homecompute.py` | Operator read-only monitoring projection | Fixed host reads and optional private n8n HA metadata projection; default HA disabled; no notifications or recovery actions |
| `openclaw_infrastructure.py` | On-demand infrastructure metadata | Fixed system selection, fresh health/update evidence and prepared native investigation context; no control or outbound delivery |
| `initialize-compute-secrets.py` | Invoked by compute setup | Symlink-safe exclusive secret initialization |

The compute scripts default to safe, staged operation. Run `help`, `validate`,
and `preflight` first, and read the matching node plan before a mutating
command. They refuse unresolved placeholders and avoid deleting existing
models, caches, secrets, previous release records, or the text runtime.

```bash
./scripts/setup-compute-node.sh help
./scripts/setup-compute-automation-moe.sh help
./scripts/setup-compute-flash-next.sh help
./scripts/setup-compute-automation-nvidia.sh help
./scripts/setup-compute-plapre.sh help
./scripts/setup-compute-hviske-stt.sh help
./scripts/setup-compute-modalities.sh help
nix flake check
nixos-rebuild build --flake .#home-core
```

The script must run from an intact repository checkout because it resolves
templates relative to their own location. Production configuration lives under
`/etc`, and runtime/model data lives outside the repository.

## Current and candidate automation model lifecycle

Production remains on Unsloth Qwen3.6-35B-A3B NVFP4 revision
`739af1e7aac320af1682ed1e0cce369af4c5265d`, served as `automation-moe`.
The exact live request tuple still needs metadata-only verification.

The Qwen3.6 and NVIDIA lifecycle instructions below are retained as dated
qualification history; do not use their cold-swap commands as current model
cutover instructions. Flash-Next profiles share one guarded lifecycle. Prepare
either immutable profile without starting it, then cold-swap one profile at a
time:

```bash
sudo ./scripts/setup-compute-flash-next.sh validate
sudo ./scripts/setup-compute-flash-next.sh install
sudo ./scripts/setup-compute-flash-next.sh status

sudo ./scripts/setup-compute-flash-next.sh validate --profile ultrafast
sudo ./scripts/setup-compute-flash-next.sh install --profile ultrafast
sudo ./scripts/setup-compute-flash-next.sh switch-profile --profile ultrafast
```

Both profiles bind the same private qualification listener and expose the
stable internal `automation-qualification` name. Select the candidate
deployment in the control-plane registry for the A/B run; never change
production semantic aliases as part of profile qualification.

`install` builds and prepares the pinned source/artifact and leaves running
models untouched. Candidate activation is a separate exclusive smoke/benchmark
step; it records and restores previous text containers and does not change
LiteLLM production routes. See
[`deploy/compute-node/README.md`](../deploy/compute-node/README.md) and
[ADR-025](../docs/adr/025-flash-next-primary-candidate.md).

## Historical Qwen3.6 qualification scripts

`setup-compute-automation-moe.sh` and `setup-compute-automation-nvidia.sh`
contain earlier vLLM profiles. Their lifecycle notes below describe past
qualification states, not the live production tuple or the new Flash-Next
candidate. Preserve their rollback source until the current service owner
reconciles them with the deployed host.
## NVIDIA Qwen3.6 production service

The NVIDIA ModelOpt production model has a dedicated vLLM 0.28.0 ARM64 image,
pinned artifact, cache manifest, Compose profile, and gateway alias. Existing
compute configurations must be migrated once so the NVIDIA tuple is included:

```bash
sudo ./scripts/setup-compute-node.sh migrate-config
sudo ./scripts/setup-compute-automation-nvidia.sh validate
sudo ./scripts/setup-compute-automation-nvidia.sh install
sudo ./scripts/setup-compute-automation-nvidia.sh activate
sudo ./scripts/setup-compute-automation-nvidia.sh smoke
```

`install` only pulls and stages. The initial `activate` command records which
text server was running, stops it, and starts NVIDIA on the exclusive automation
listener. Startup or smoke failure restores that source. NVIDIA serves both
`automation-moe-nvidia` and the stable `automation-moe` model name, so existing
gateway clients continue working during the cold swap. Its containers use a
persistent restart policy. The generated gateway route maps `automation` and
`automation-moe` to the NVIDIA deployment. If rolling back after that gateway
configuration is live, restore the previous gateway mapping before running
`deactivate`; otherwise requests would still target the NVIDIA model name.
The 2026-09-29 production cutover removed the old Unsloth containers and the
activation rollback marker. Do not run `deactivate` for this deployment.
The qualification results and their workflow guards are recorded in
`docs/nvidia-qwen36-automation-qualification-2026-09-29.md`.

## sparkrun model manager

`sparkrun-model-manager.py` is a one-shot JSON command boundary for an
authenticated `home-core` dashboard. Run it on `home-spark` as root, normally
through a fixed SSH command and narrowly scoped sudo rule. It accepts no
command-line arguments and reads one JSON object from stdin. The caller can
select only deployment IDs in `config/model-catalog.json`; hosts, model IDs,
revisions, recipes, images, ports, runtime options, and shell text are never
accepted from the request.

Supported requests:

```json
{"action":"list"}
{"action":"status"}
{"action":"prepare","deployment":"automation-spark-primary"}
{"action":"load","deployment":"automation-spark-primary"}
{"action":"unload","deployment":"automation-spark-primary"}
{"action":"replace","from":"automation-spark-primary","to":"home-spark-primary"}
```

Every response is one JSON object. Success uses `{"ok":true,"data":...}`;
failure uses `{"ok":false,"error":{"code":...,"message":...}}`.
`list` reports all catalog deployments with an eligibility flag. `status`
reports sparkrun state and `port_available` for the known catalog recipes.
`prepare` runs only the
existing pinned-artifact and accepted-cache workflow, then writes and validates
a root-owned local sparkrun recipe. `load` verifies the accepted cache again,
refuses a catalog port already used by a Compose or other listener, launches
the generated recipe with fixed sparkrun arguments, and runs the existing
HomeCompute health, auth, protocol, and tool qualification smoke. A failed
smoke stops the new workload. `replace` restores its source if the target
fails. Unload retains model caches.

Only the qualified `automation-spark-primary` and `home-spark-primary`
deployments are mutable. `general-spark-qwen38` remains excluded while its
catalog lifecycle is disabled and qualification is empty. Existing Compose
services remain the production workloads; while they own a catalog port,
sparkrun `load` safely refuses to start there. The helper does not perform a
live migration.

Generated recipes preserve catalog model commits and NVIDIA image digests,
mount the accepted cache and API-key secret at fixed paths, and disable
Hugging Face network fallback. Sparkrun does not expose all of Compose's
container hardening controls, including a read-only root filesystem and
dropping every Linux capability. Treat this manager as a migration adapter
pending security and gateway-routing qualification; do not give the dashboard
direct Docker access or a general-purpose shell.

### Provisioning the SSH boundary

The provisioning helper does not install Sparkrun or configure its cluster.
Those steps must already be complete for root on `home-spark`, using the
[official Sparkrun install/setup process](https://sparkrun.dev/getting-started/installation/).
The documented install command is `uvx sparkrun setup install`; use the
Sparkrun setup wizard to configure the root-owned `home-spark` cluster. Ensure
the resulting protected executable is at `/usr/local/bin/sparkrun` or
`/root/.local/bin/sparkrun`, then verify it with:

```bash
sudo /usr/local/bin/sparkrun cluster show home-spark --json
```

If installed at `/root/.local/bin/sparkrun`, substitute that path. Keep its
root configuration and cluster SSH credentials protected; the dashboard
account does not receive access to them.

On `home-core`, make a dedicated client key if one does not already exist:

```bash
ssh-keygen -t ed25519 -f ~/.ssh/homecompute-sparkrun-manager \
  -C sparkrun-manager@home-core
```

Add the manager credentials, private key, and pinned Spark host key to the
encrypted `secrets/home-core.sops.yaml` document with `sops`. Use these nested
keys; the private SSH key and operator password must never be added as
plaintext:

```yaml
model-manager:
  username: <operator username>
  password: <operator password>
  spark-ssh-key: |
    -----BEGIN OPENSSH PRIVATE KEY-----
    ...
    -----END OPENSSH PRIVATE KEY-----
  known-hosts: |
    192.168.30.126 ssh-ed25519 <base64 host public key>
```

Get the host public key from the Spark's `/etc/ssh/ssh_host_ed25519_key.pub`
and verify its fingerprint at the trusted Spark console before adding it.
SOPS-Nix materializes these as root-only files under
`/run/secrets/model-manager/` during activation. The Home Core deployment keeps
the manager stopped until all four are present and the forced-command
readiness flag is explicitly enabled.

Transfer only its public half to `home-spark`, then place it in the root-only
source file. The following commands assume `home-spark` is the SSH host alias
and that the compute setup has created `/etc/gb10-ai/secrets`:

```bash
scp ~/.ssh/homecompute-sparkrun-manager.pub home-spark:/tmp/sparkrun-manager.pub
ssh home-spark 'sudo install -o root -g root -m 0600 /tmp/sparkrun-manager.pub /etc/gb10-ai/secrets/sparkrun-manager.pub && rm -f /tmp/sparkrun-manager.pub'
```

On `home-spark`, after deploying a trusted immutable HomeCompute release and
securely installing/configuring Sparkrun for root:

```bash
sudo /srv/homecompute/current/scripts/setup-compute-sparkrun-manager.sh validate
sudo /srv/homecompute/current/scripts/setup-compute-sparkrun-manager.sh install
```

The helper validates the one-line Ed25519 public key and installs a locked
`sparkrun-manager` account with exactly one `authorized_keys` entry. That key
is forced to the no-argument root wrapper at
`/usr/local/sbin/homecompute-sparkrun-model-manager`; sudo permits only that
wrapper with no arguments. The wrapper forwards JSON stdin to the adapter in
the active release. Its JSON action and response contract is the one described
above. The helper never reads or writes private-key material and does not
start an HTTP service.

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

## OpenClaw private conversation

`python3 scripts/openclaw-chat.py chat --conversation main` opens the authenticated
operator console for the synthetic canary; `status` checks its native gateway.
The scoped adapter and inactive n8n conversation/notification workflows are
described in [the deployment and rollback proposal](../docs/openclaw-communication.md).
External delivery requires a selected authorized destination and activation approval.

`openclaw-telegram.py` prepares the selected dedicated Telegram phone chat.
It polls outside the assistant with pinned bot/chat/human identities, uses a
durable update cursor and queues replies through the same n8n outbox. `inspect`
shows sender candidates without enrolling them; `run` requires approved private
credentials and configuration. See the phone setup in the same deployment proposal.

`setup-codex-vm.py` provisions only the approved dedicated Codex KVM guest on
home-core, from the pinned Ubuntu image with separate root/work disks and host
identity. It refuses existing disk/config replacement. `--lock-egress` closes
bootstrap public HTTP(S)/DNS; `--stop` retains disks and evidence. See the
[actual VM installation and rollback](../docs/openclaw-runtime-research.md#approved-dedicated-vm-provisioning-2026-10-09).
