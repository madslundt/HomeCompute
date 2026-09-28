# Configuration templates

This directory contains source-controlled examples for operator-owned
configuration. The setup scripts copy these templates outside the repository;
they do not use them as production configuration in place.

| File | Used by | Purpose |
| --- | --- | --- |
| `compute-node.env.example` | `setup-compute-node.sh`, `setup-compute-modalities.sh` | Immutable text and staged modality artifact tuples, exact private ports, bind policy, and compute limits |
| `plapre-tts.env.example` | `setup-compute-plapre.sh` | Pinned Plapre/Kanade/HiFT tuple, Danish voice gate, resource bounds, and Wyoming publication policy |
| `hviske-stt.env.example` | `setup-compute-hviske-stt.sh` | Pinned Hviske v5.3 tuple, license decision, bounded transcription, and Wyoming publication policy |
| `model-catalog.json` | `scripts/model_registry.py` and the LiteLLM renderer | Immutable text artifacts, supported runtime profiles, deployment identities, endpoint environment names, lifecycle, and qualification evidence |
| `capability-routes.json` | `scripts/model_registry.py` and the LiteLLM renderer | Stable capabilities, deployment mapping, local-only policy, context requirements, and named timeout profiles |
| `gb10-model-roster.json` | `gb10_model_roster.py` and GB10 installation planning | Hardware-scoped model and modality staging inventory; generic structural validation only |
| `speech-routing-policy.json` | `speech_routing_policy.py` and future speech adapters | Inactive Danish/non-Danish STT/TTS routes, fallback behavior, and license/voice gates |
| `tts-qualification.json` | `tts-qualification.py` | Danish phrase set, 750 ms/RTF/listening gates, and ASR/resynthesis recovery scenarios |
| `control-plane.env.example` | `deploy/control-plane/compose.yaml` | Immutable gateway images, explicit bindings, selected compute transport, `/srv/state`, and sops-nix runtime secret paths |
| `open-webui.env.example` | `deploy/open-webui/compose.yaml` | Pinned Open WebUI image, persistent state path, and runtime LiteLLM/WebUI secret-file paths |
| `model-manager.env.example` | `deploy/model-manager/compose.yaml` | Local build tag, exact public origin, fixed Spark SSH target, and external secret-file paths |
| `homepage.env.example` | `deploy/homepage/compose.yaml` | Pinned Homepage image, explicit LAN/Tailscale bindings, and allowed hostnames |
| `wyoming-stt.env.example` | `deploy/wyoming-stt/compose.yaml` | Pinned CPU STT image, Danish `small-int8` settings, explicit LAN binding, and bounded resources |
| `books_importer.env.example` | `deploy/books_importer/compose.yaml` | Pinned book service images; compare with source deployment digests before migration |
| `books_importer-secrets.env.example` | `deploy/books_importer/compose.yaml` | Reference for the encrypted SOPS books_importer/environment entry |

Every `REPLACE_WITH` value is intentional. Validation fails while placeholders
remain. Files are parsed by a strict allow-list loader and are never executed
as shell code. They still control privileged operations, so use only trusted
operator input and never add shell commands.

Do not store tokens, API keys, private SSH keys, real environment files, or
site-specific secrets here. Compute-node secrets belong under
`/etc/gb10-ai/secrets`; control-plane secrets are materialized under
`/run/secrets/control-plane` by sops-nix and never belong in an environment
file. Open WebUI and model-manager runtime file paths are also listed in their
environment templates, but their credentials are not declared in the current
SOPS document; provision them outside Git before enabling either UI. The
optional compute SSH fallback key and pinned host-key file live under root-owned
`/etc/homecompute/compute-tunnel`; only their paths and transport selection are
configuration.

`model-catalog.json` and `capability-routes.json` are the source of truth for
the LiteLLM text route section. `scripts/model_registry.py render` regenerates
only `model_list`; `check` detects drift. Authentication, key scopes, logging,
database, and network settings remain explicit in LiteLLM configuration and
are not inferred from registry entries. Candidate deployments do not become
stable capability routes automatically.

The GB10 roster remains an installation plan for text and speech artifacts;
its validator checks immutable references and internal consistency rather than
requiring a fixed set of model winners. `setup-compute-node.sh` still has a
guarded model-specific workflow and is scheduled for a later incremental
`modelctl` migration. Do not treat catalog membership alone as a production
promotion or as permission to add a firewall route.

The checked-in timeout values are initial operational budgets, not measured
final latency SLOs. Recent isolated Danish text/tool calls completed in under a
second, but production p95/p99 and silent-stream behavior still need measured
failure-injection results before declaring those budgets qualified.

Configuration records artifact identity only. Never place API keys, audio,
images, rendered document pages, transcripts, or responses in the environment
file. Vision accepts rendered image pages, not native PDF input. Modality
release records are secret-free but remain staged evidence until live
`home-spark` qualification succeeds.

The text lifecycle atomically migrates the exact legacy single-service schema
when `init`, `install`, or `up` first encounters it. Existing values are
retained, the removed firewall-confirmation flag is dropped, and every unknown
key is rejected rather than guessed. `rollback` inputs remain immutable and
must already use the current schema.

The books_importer stack uses Compose's environment-file parser directly, rather than
the setup scripts' allow-list loader. Copy `books_importer.env.example` to
`/etc/homecompute/books_importer/runtime.env`. Keep the directory root-owned with mode
`0700` and the file root-owned with mode `0600`. Credentials are stored in the
encrypted `books_importer/environment` entry in `secrets/home-core.sops.yaml`. On home-core,
edit that file from the repository root using:

```bash
sudo env SOPS_AGE_KEY_FILE=/var/lib/sops-nix/key.txt \
  sops secrets/home-core.sops.yaml
```

Fill the empty values in the `books_importer.environment` dotenv string, keeping the other
entries intact. `CWA_USERNAME` defaults to `admin`. Save through SOPS so only
ciphertext is written back, and bring the encrypted file back into your Git
checkout if editing on the host. Do not edit the ciphertext in a normal editor.
After deploying the updated configuration with `nixos-rebuild switch`, sops-nix
creates `/run/secrets/books_importer/environment`, owned by root with mode `0400`.
Never read the plaintext into a Nix expression. Blank required values cause
Compose validation to fail. Credentials are supplied only to
`shelfmark-automated`. They remain
visible to Docker administrators through container inspection. No `_FILE`
credential support is assumed for this application.

Move the five HAOS books_importer directories (`cwa_config`, `library`, `import`,
`shelfmark_config`, `sa_data`) from `/mnt/data/supervisor/share/books/` to
`/srv/state/books_importer/`, keeping their names and ownership. Stop the source services
before the final data copy. The supplied root PUID/PGID settings are preserved.
Start the two web applications first and verify their restored accounts, then
start the automation, which synchronizes on startup:

```bash
sudo docker compose --env-file /etc/homecompute/books_importer/runtime.env \
  --env-file /run/secrets/books_importer/environment \
  -f deploy/books_importer/compose.yaml config --quiet
sudo docker compose --env-file /etc/homecompute/books_importer/runtime.env \
  --env-file /run/secrets/books_importer/environment \
  -f deploy/books_importer/compose.yaml up -d cwa shelfmark
# After verifying both web applications:
sudo docker compose --env-file /etc/homecompute/books_importer/runtime.env \
  --env-file /run/secrets/books_importer/environment \
  -f deploy/books_importer/compose.yaml up -d shelfmark-automated
```

Run these commands from the repository root on home-core. Use `config --quiet`:
the ordinary `config` output includes resolved credentials. From your workstation,
run `ssh -L 8083:127.0.0.1:8083 -L 8084:127.0.0.1:8084 mads@home-core`,
then open `http://localhost:8083` and `http://localhost:8084`. Normal LAN and
Tailscale access uses the hostname or address chosen for Homepage on ports 8083
and 8084; the Compose project binds only those explicit host addresses plus
loopback. Internal service connections use Docker DNS (`http://cwa:8083` and
`http://shelfmark:8084`).
Docker restart policies restart existing containers after a reboot; this stack
does not yet have a NixOS systemd unit to reconcile Compose changes.
