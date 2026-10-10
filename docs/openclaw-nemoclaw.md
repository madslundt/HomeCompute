# OpenClaw in the existing NemoClaw guest

**Recommended target:** a separate `agent-openclaw` OpenShell sandbox inside
the existing agents VM, alongside the Hermes sandbox. Keep the standalone
Docker stack disabled as an isolated validation fallback. This preserves the
existing VM/kernel boundary and adds OpenShell filesystem/process/egress
policy; it does not replace the broker's approval authority.

The operator approved a **synthetic-only live canary** on 2026-10-09.
The canary was created on the separate gateway 9123; its scoped
`automation-moe` inference smoke passed. The attempted external supervisor was
rolled back; its two external-owner systemd units are disabled. The active owner is the
supported NemoClaw-managed detached lifecycle. Hermes remains on gateway 8080 with
its unchanged `assistant-canary` route. Broker tools remain denied and
no household data or publishing credentials are enabled. The
[standalone evidence](openclaw-runtime-validation.json) is separate evidence.

On 2026-10-10, the operator approved recovery and automatic restart. The agents
VM now has 24 GiB; OpenClaw and Hermes each have an 8 GiB hard memory limit,
and Chromium has 4 GiB. These containers have no additional swap allowance.
The restored OpenClaw workspace and conversation database retain the existing
tool restrictions and the replacement's newly issued native authentication.
`homecompute-openclaw-supervisor.service` supervises the supported native CLI
with an exact sandbox UUID and lifecycle-generation fence. It retains the
NemoClaw-managed gateway owner; the failed external-owner units stay disabled.
Graceful stops recover through native `start`. The pinned runtime turns a main
process crash into terminal `Error`; its supervisor captures and verifies a
complete private backup before supported native replacement. It restores the
workspace, canonical conversation database and disabled broker, retaining fresh
native authentication and the 8 GiB limit. Recovery checks offline file hashes
and then stable conversation IDs and transcript hashes after startup. A failed
transaction blocks further automatic replacement for operator reconciliation;
successful replacements are limited to one per hour. No agent turn is replayed.
Fresh local CLI pairing is qualified through a read-only native admin RPC. Only
the initiating pending request may be approved, and its local identity must
match the native identity store and an already paired CLI device. Other pending
devices and scopes are refused. During recovery only `session_status` is
callable; memory, writes and external tools remain denied.
The system shutdown hook stops the watchdog and then records native `Stopped`
before Docker, the user manager, SSH and login sessions are torn down. On boot,
the watchdog uses the supported native credential-provider query to recover the
named managed host gateway, then starts an exact retained Stopped sandbox.
This path avoids creating an onboarding inference-route reservation. Readiness
prefers the dashboard's `/readyz`; when its optional host forward is absent, an
authenticated read-only native admin RPC proves the canonical CLI is ready.
The managed host gateway runs in the watchdog user-service cgroup; ordinary
watchdog restarts preserve it.

The second reboot qualification retained an incomplete native onboarding route
reservation. Canonical CLI model turns and Telegram replies were verified with
that marker present; the optional dashboard forward is absent. Native
`connect`/`recover` and administrative rebuild finalization refuse the marker.
Native stop/start still change the exact sandbox phase, so supervision accepts
positive `Stopped`/authenticated `Ready` even when optional CLI finalization
returns nonzero. The pinned native destroy cleanup authority was qualified
read-only against this exact pending entry; its supported cleanup removes the
owning registry row before future Error replacement onboarding. The supervisor
preserves the reservation and never fabricates native checkpoint ownership.

The 2026-10-10 boot qualification exposed an unfinished native onboarding
checkpoint and inference-route reservation after a stopped-sandbox onboarding
attempt. They remain intact; no ownership markers or native phases were edited.
Canonical chat and Telegram work, but optional dashboard forwarding and native
connect/recover finalization remain limited by that checkpoint. Boot recovery
now uses the supported credentials query rather than onboarding. Exact observed
native `Stopped`/`Ready` plus authenticated readiness take precedence over a
nonzero CLI exit caused solely by finalization. The scoped native destroy path
removes the owning registry row before a future verified-backup replacement.

The VM allocation is also saved in `hosts/home-core/default.nix`. Its narrow
live change uses `/etc/homecompute/agents-vm-start-24g` and the persistent
`/etc/systemd/system.control/homecompute-agents-vm.service.d/90-openclaw-memory.conf`
override. After deploying the matching NixOS configuration, remove that live
override so subsequent VM unit changes come from NixOS.

## Verified tuple and artifacts

The repository's existing Hermes tuple selects NemoClaw `v0.0.129`, source
`26922313bba96184e65c3663b351683ebae9504d`, installer SHA-256
`738cb07356dc5638ca91351ef78c1e0af7cb689a4bdf579726408306e282e72c`, and
OpenShell `0.0.116`. That exact source's OpenClaw manifest and Dockerfiles
select **OpenClaw 2026.9.1**, not the standalone prototype's 2026.9.9.
Do not upgrade OpenShell or the managed OpenClaw runtime independently.
[Release source](https://github.com/NVIDIA/NemoClaw/tree/26922313bba96184e65c3663b351683ebae9504d),
[agent manifest](https://github.com/NVIDIA/NemoClaw/blob/26922313bba96184e65c3663b351683ebae9504d/agents/openclaw/manifest.yaml).

The official GHCR tag `ghcr.io/nvidia/nemoclaw/sandbox-base:v0.0.129` resolved
to OCI index `sha256:867d041dc7a530faba73b333548b7ac7909655c39aaa9214df326c540e95fef7`.
Its amd64 manifest is `sha256:d364064ab4fbb003b4041a1af92d9632d1da36d385d748db84ad8c95b35c346f`;
the image's revision label matches the full source commit. It contains Node
24.18.1. This is the legacy Dockerfile base artifact, not a managed workload
override. Live onboarding rejected `NEMOCLAW_SANDBOX_BASE_IMAGE_REF` because
the managed workload owns its exact pre-verified digest. That override was
removed from the template before resuming; never force a legacy workload to
bypass the check. The actual managed image is
`ghcr.io/nvidia/nemoclaw/openclaw-sandbox@sha256:51d9fe7e0097931ce96f72f3c7dea522c129c569d91680d2e11989ea70a37d8a`,
with the exact source revision above. The legacy community blueprint's different sandbox
image digest is not the managed base-image pin used here.
[Base resolver source](https://github.com/NVIDIA/NemoClaw/blob/26922313bba96184e65c3663b351683ebae9504d/src/lib/sandbox-base-image.ts).

## Separate gateway and local inference

Use `NEMOCLAW_GATEWAY_PORT=9123` with every command for this canary.
It selects gateway `nemoclaw-9123` and a separate host registry/snapshot root
`~/.nemoclaw/gateways/9123/`. Hermes keeps its existing gateway and
`assistant-canary` route. This matters because OpenShell inference selection is
gateway-scoped: changing a shared gateway can affect its other sandboxes.
[Multiple gateways](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/manage-sandboxes/operate-sandboxes/run-sandboxes),
[pinned CLI reference](https://github.com/NVIDIA/NemoClaw/blob/26922313bba96184e65c3663b351683ebae9504d/docs/reference/commands.mdx).

The upstream is the **existing** host-only compatibility bridge
`http://ai.home.arpa:18080/v1`, with a separate virtual key restricted to
`automation-moe`. It is not a new inference server. The NixOS bridge binds only
the private agents link and verifies Caddy's TLS/hostname on its upstream leg.
It already exists because the pinned OpenShell inference client did not consume
the imported private CA. Inside OpenClaw, the route remains
`https://inference.local/v1`, primary `inference/automation-moe`, and the provider
credential is the documented **`unused` sentinel**. OpenShell holds/injects the
real upstream key outside the sandbox. Do not copy the standalone model key or
its `model-relay` Docker DNS name into this config.
[Existing bridge](../modules/nixos/agents-vm.nix),
[managed inference](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/about/how-it-works).

Custom non-default gateway ports use a detached process under the pinned
NemoClaw-managed lifecycle; only the default port receives its normal managed
user service. Therefore **reboot supervision is still a gate**. For a durable
service, use the documented externally-supervised gateway declaration and one
systemd supervisor, validating listener identity/cgroup/health; do not add a
second owner behind NemoClaw. Reviewed unit/declaration templates are under
`deploy/openclaw/nemoclaw/` and `config/openclaw-gateway-management.json`;
they remain disabled after the qualification attempt described below.
[Lifecycle authority](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/deployment/gateway-lifecycle-authority).

## Declarative configuration

- `config/openclaw-nemoclaw.env.example`: secret-free supported onboarding
  inputs, distinct ports, native immutable image selection, Restricted policy,
  `skip` optional presets, direct tools, no GPU/search/channels, one CPU and
  8 GiB RAM. A fresh pinned CLI run rejects a blank custom preset list;
  narrow the live base network policy separately to `managed_inference` only.
- `config/openclaw-nemoclaw-agents.json`: JSON accepted as a YAML `--agents`
  manifest. It restricts the primary agent **at image generation**, before any
  post-onboarding config operation. No secondary agent is enabled.
- `config/openclaw-nemoclaw.json`: OpenClaw 2026.9.1 **overlay**, not a complete
  replacement for NemoClaw's generated config. It scopes memory/workspace,
  disables general runtime/process/web/device/admin tools, and keeps all three
  broker tools explicitly denied and the custom plugin disabled.
- `scripts/prepare-openclaw-nemoclaw.py`: prepares a new private candidate
  without invoking any service, upload or installer. It preserves the managed
  gateway auth, port, proxy and native plugin install ownership; refuses real
  inference keys/additional providers; removes the generated `alsoAllow`
  MCP grant before applying the finite allowlist; refuses existing outputs.

The pinned generator actually emits `agents.entries`, despite an older docs
passage saying `agents.list`. Native 2026.9.1 validation was used to resolve
that discrepancy. `gateway.uploads` from 2026.9.9 is unsupported and is omitted;
that newer upload-disable setting is not claimed for this target. Memory uses
native `memory-core`, keyword-only recall, and private Markdown workspace
writes. Workflow/task/delivery state remains in n8n/the broker.
[Generator](https://github.com/NVIDIA/NemoClaw/blob/26922313bba96184e65c3663b351683ebae9504d/scripts/generate-openclaw-config.mts),
[declarative agents](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/inference/declarative-agents-manifest).

## Operator-only recipe after approval

First inspect the VM's backup/network readiness, disk capacity, existing
registries, free ports and pinned CLI/runtime versions. Provision the dedicated
model key outside the sandbox and review the template. Do not run an installer
against the existing Hermes guest merely to select the OpenClaw agent.
If the existing CLI tuple is correct, reuse it. Keep dashboard
forward 18791 on guest loopback. The gateway listens on loopback plus the
internal Docker bridge required by the native driver, with mTLS; use the existing SSH/Tailscale access path.

The supported create command, with reviewed template variables exported and
`COMPATIBLE_API_KEY` loaded privately from its separate file, is:

```bash
nemoclaw onboard --non-interactive --yes-i-accept-third-party-software \
  --agents /etc/homecompute-openclaw/openclaw-nemoclaw-agents.json
```

This is a mutating, privileged management operation requiring operator
approval. It creates only the synthetic canary. Do not export unrelated API,
messaging or search tokens. The template is not automatically sourced or
invoked by any service in this repository.

After approved creation, use the exact scoped grammar:

```bash
NEMOCLAW_GATEWAY_PORT=9123 nemoclaw agent-openclaw exec -- openclaw --version
NEMOCLAW_GATEWAY_PORT=9123 nemoclaw agent-openclaw status --json
NEMOCLAW_GATEWAY_PORT=9123 nemoclaw agent-openclaw doctor
```

`status`/`start` can send real local-model inference probes; they are not pure
metadata reads. Verify the live route is `automation-moe` and Hermes's route
is unchanged. Save sanitized evidence before progressing.

A local plugin installation is officially supported without registry access
for this dependency-free package:

```bash
NEMOCLAW_GATEWAY_PORT=9123 nemoclaw agent-openclaw upload \
  ./deploy/openclaw/broker-plugin /sandbox/
NEMOCLAW_GATEWAY_PORT=9123 nemoclaw agent-openclaw exec -- \
  openclaw plugins install /sandbox/broker-plugin --force --accept-capabilities
NEMOCLAW_GATEWAY_PORT=9123 nemoclaw agent-openclaw exec -- \
  openclaw plugins disable homecompute-broker
```

Stage outside the native extensions directory. OpenClaw owns the install index;
do not hand-write it. Keep the primary agent's broker denies in force during
installation and leave this plugin disabled until the adapter gate below.
[Native plugin lifecycle](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/deployment/install-openclaw-plugins).

Download the managed config into a private operator directory, prepare a new
candidate using the helper, validate it with the exact in-sandbox CLI, and
apply only after reviewing its diff. The preparation command is local-only:

```bash
python3 scripts/prepare-openclaw-nemoclaw.py \
  --managed-config /tmp/private-openclaw/current.json \
  --gateway-port 18791 \
  --output /tmp/private-openclaw/candidate.json
```

The native gateway port is derived from `NEMOCLAW_DASHBOARD_PORT`: it is
18791 in this actual managed profile, not the manifest's default 18789.
The helper refuses a mismatch and preserves that selected port. Uploads append
the source basename to their destination directory; inspect the resulting path.

Use NemoClaw's supported upload/exec and `gateway restart` lifecycle to apply
reviewed config; preserve the current auth/proxy/native install records and
verify its permission/hash contract. The candidate may contain the existing
machine-local gateway token, so keep it mode0600 and never commit or print it.
Do not wholesale upload the overlay as `openclaw.json`. Snapshot/rebuild/restore
must retain the restrictive policy; the acceptance test must check this rather
than assuming the build generator preserves arbitrary user config fields.

## Broker adapter remains blocked

The three dependency-free tools were loaded and registered successfully under
both official OpenClaw 2026.9.1 and 2026.9.9. That verifies the plugin API,
**not** managed network access. The current transport fixes
`http://broker:8080` and reads a raw broker token from the standalone Gateway
environment. Neither the Docker service DNS nor that secret placement is a
supported NemoClaw integration. It must remain disabled here.

The supported choices are a narrow HTTPS broker origin with an OpenShell
endpoint-bound credential provider/policy and a resolver placeholder, or a
three-tool HTTPS Streamable HTTP MCP adapter added through `nemoclaw <name>
mcp add`. Managed MCP already supplies credential placeholders, literal-path
policy, private-host address pins and differential injection probes. The broker
now includes a finite Streamable HTTP `/mcp` adapter; ten local tests passed,
including official SDK initialize/list/submit/status/cancel and a bounded
100-task status list. Its production activation remains blocked by a stable
private HTTPS terminator exposing **only `/mcp`**, an endpoint-bound OpenShell
credential provider/policy and approved dedicated assistant credential. See
[operations](openclaw-operations.md) for current adapter state. Do not add
an unrestricted n8n MCP server, direct production network, agent-visible raw
key, arbitrary CONNECT proxy, or operator/publisher credential to make it work.
[Managed MCP contract](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/manage-sandboxes/mcp-servers/add-an-mcp-server),
[credential isolation](https://docs.nvidia.com/nemoclaw/user-guide/openclaw/security/credential-storage).

The raw operator approval and GitHub publishing credentials must remain in the
trusted broker/publisher boundary. OpenShell egress approval is permission to
reach an endpoint; it is not authorization to execute a task, publish a PR,
deploy, mutate personal systems or control physical devices.

## Evidence and remaining gates

Initial isolated validation:

- Exact NemoClaw commit, managed version pins, supported flags and generator.
- Official base-image registry digest/platform/source label.
- Native `openclaw@2026.9.1` config validation: valid, zero warnings for the
  overlay after temporary local path substitution. The existing managed
  `nemoclaw` plugin entry was excluded from this Mac-only validation because
  its built module exists in the managed image, not this source checkout.
- Native 2026.9.1 broker plugin inspection: loaded, exact three optional tools,
  no diagnostics/dependencies/hooks/HTTP routes. Temporarily enabled only in
  isolated validation config; committed managed overlay remains disabled.
- Pinned source `validateExtraAgents`: primary finite policy accepted.
- Five preparation-boundary tests and seven broker-plugin tests pass; helper
  compiles and only prepares a new private file.

Live synthetic evidence is recorded in [the managed receipt](openclaw-nemoclaw-validation.json):

- Native 2026.9.1 validates the deployed overlay with zero warnings; managed
  proxy, tool scopes, memory and native plugin install ownership are preserved.
  Fresh onboarding rotated machine-local Gateway auth; its native-issued auth
  is authoritative, with the prior private archive retained.
- Model route is `inference/automation-moe`; only `write`, `session_status`,
  `memory_search` and `memory_get` are exposed. Broker plugin is installed but
  disabled; all three broker tools are denied, with no raw broker token.
- After rollback and restart, a native read-only `memory_get` turn succeeded
  with exit 0, `replayInvalid=false`, the exact model route and synthetic
  marker. The memory file hash remained unchanged.
- Synthetic memory write applied and the file hash is recorded. The write
  turn returned native status `ok` and a successful `write` receipt, but
  NemoClaw exited 1 for `replayInvalid=true`: the pinned native runtime marks
  potential side effects unsafe to replay. Do not retry it automatically.
- Effective Docker limits: one CPU, 2 GiB RAM, 4 GiB total RAM plus swap;
  non-privileged container with writable rootfs, AppArmor `unconfined`,
  `NoNewPrivs=1`, `Seccomp=2`. Landlock logs show ABI V2, 14 rules applied,
  one skipped, compatibility `best_effort`; no strict fail-closed claim.
- Live network policy was narrowed to only `managed_inference`. Public
  `example.com` and direct core/Spark access failed. `/etc` write failed;
  the host model-key file and Docker socket were absent in the sandbox.
- Hermes remains on its original container, gateway 8080 and
  `assistant-canary` route version 10. No Docker daemon change, upgrade,
  guest reboot or household data was introduced.

The dedicated credential permits only `automation-moe`, rejects another alias
with HTTP 403, permits one parallel request, six requests/minute and 65,536
tokens/minute, and **expires 2026-10-16 16:15:04 UTC**. The key bytes and master
credential were never printed or committed. Renewal is an operator action.

### Attempted supervision and rollback

Only gateway 9123 was stopped and its SQLite/TLS/config state copied privately
outside NemoClaw-owned directories. The external systemd user unit passed
listener/cgroup identity and mTLS health; native doctor reported `ok`, zero
warnings. A bounded service restart also restarted its gateway successfully.

NemoClaw correctly refused subsequent forward effects because its onboarding
checkpoint still recorded the prior managed owner. The exact source requires
fresh onboarding to change owner. That supported path then failed
`host.docker.storage_incompatible` on the VM's existing Docker 29.1.3
`overlayfs`/containerd snapshotter, despite the working gateway using the
native Docker compute driver. No preflight bypass, source patch, Docker
storage change or runtime upgrade was attempted.

Both `homecompute-openclaw-gateway.service` and
`homecompute-openclaw-canary.service` were disabled. Latest private state was
restored to the original canary location, the external declaration renamed
`gateway-management.blocked.json`, and supported fresh managed onboarding
completed. It regenerated the local Gateway auth. A config-only attempt to
restore the prior auth failed native restart with token mismatch and was
reverted to the native-issued working auth; no further auth migration was
attempted. Neither the scoped model credential nor Hermes auth changed.
Archived private state remains available for operator rollback;
no canary or Hermes state was deleted. **Boot supervision is blocked** until
this exact pinned compatibility issue has a supported resolution. The staged
units/declaration must not be enabled as if qualification had passed.

Remaining production gates: supported reboot supervision and a reboot drill;
rebuild/restore preservation of tool denies and credential isolation;
encrypted off-host backup/restore; prompt-injection acceptance; qualified
managed MCP credential adapter; and real Codex/GitHub approval/publishing
acceptance. Keep household data and autonomous actions disabled.

### Access, stop and rollback

The dashboard forward stays on guest loopback. From the Mac, use the existing
operator key and jump host:

```bash
ssh -N -J home-core -i ~/.ssh/id_ed25519_ai-services-01 \
  -o IdentitiesOnly=yes -L 18791:127.0.0.1:18791 \
  hermes-operator@10.77.20.2
```

Open `http://127.0.0.1:18791` and use native local pairing/authentication. Never
publish an authenticated dashboard URL or token in a report.

Canary kill switch, run in the guest as its operator:

```bash
PATH="$HOME/.local/bin:$PATH" NEMOCLAW_GATEWAY_PORT=9123 \
  nemoclaw agent-openclaw stop
```

This preserves workspace state and does not target Hermes. Also revoke the
**dedicated canary** virtual key through the trusted LiteLLM administrator if
credential access must end immediately; do not revoke shared keys.

For this attempted supervisor only, the safe disabled state is:

```bash
systemctl --user disable --now homecompute-openclaw-canary.service
systemctl --user disable --now homecompute-openclaw-gateway.service
unset NEMOCLAW_GATEWAY_MANAGEMENT
PATH="$HOME/.local/bin:$PATH" NEMOCLAW_GATEWAY_PORT=9123 \
  nemoclaw agent-openclaw doctor --json
```

The completed state migration rollback was performed with the canary gateway
stopped, an offline private copy of its **latest** SQLite/TLS/config state,
a retained backup of the previous directory and a fresh supported managed
onboarding run. Do not blindly rerun that filesystem migration or edit the
trusted lifecycle checkpoint. `start`/`doctor`/`status` can send model probes;
respect the scoped credential's pilot limits.
