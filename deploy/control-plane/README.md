# NixOS control-plane workload

This is the deliberately small first stack for `home-core`: Caddy, one
LiteLLM process, and a PostgreSQL instance dedicated to LiteLLM.

## Why this split

| NixOS host | This Compose project | Separate Compose projects, same host | Separate host |
| --- | --- | --- | --- |
| SSH, time sync, updates, disk health, networking, firewall, backup transport, Docker Engine, sops-nix secrets, `/srv/state` | Caddy, one LiteLLM worker, dedicated PostgreSQL | n8n, browser workers, agent/code sandboxes, toolbox builds | Home Assistant, GB10 inference |

Redis is deferred entirely, not relocated; see below.

The third column shares this host's kernel. [ADR-017](../../docs/adr/017-consolidated-application-host.md)
accepts that because no fourth machine exists. Those projects must not join the
`internal` network defined here, must not mount the Docker socket, and must
read their secrets from a different sops group — otherwise the split in this
table is a naming convention rather than a boundary.

PostgreSQL is present because independently revocable LiteLLM virtual keys are
a day-one requirement. LiteLLM's bootstrap database account is separate from
its non-superuser application owner. Redis is not needed for one worker and
would otherwise introduce another credential and prompt/response retention
surface. Add a LiteLLM-dedicated Redis/Valkey only before enabling multiple
workers/replicas or after explicitly approving response caching and retention.

## Model routes

The LiteLLM `model_list` is generated from
[`config/model-catalog.json`](../../config/model-catalog.json) and
[`config/capability-routes.json`](../../config/capability-routes.json):

```bash
python3 scripts/model_registry.py validate
python3 scripts/model_registry.py check
python3 scripts/model_registry.py render
```

The renderer changes only the `model_list` block. Key scopes, authentication,
privacy/logging, database configuration, retries, and transport remain explicit
and reviewed. `automation-moe` is the only exposed text alias. The former
`automation`, `home`, `automation-moe-nvidia`, and `automation-qualification`
aliases selected candidate or qualification listeners; they are omitted from the
gateway model list. The artifact remains marked candidate because its full
quality qualification is incomplete. Disabled aliases backed by stopped models
are omitted. The cold CPU automation standby remains an operator-run
maintenance action.

`automation-moe` has a per-deployment request budget of 600 seconds. This value
is an initial budget, not a final p95/p99-based SLO. Verify timeout
behavior against the image pinned in `config/control-plane.env.example` with
synthetic refused, non-responsive, error, and streaming upstreams before
promoting the generated config. Model generation does not create, broaden, or
revoke any virtual key.

The generated model list assigns explicit zero upstream cost to local text,
embedding, transcription, speech, and video models. LiteLLM's Usage view can
therefore account for every request without mistaking local inference for an
API bill. This is provider spend, not electricity, hardware amortization, or a
household charge.
Use LiteLLM Admin at `/ui/` → Usage for spend by model and virtual key. The
separately hosted token-count dashboard and refresh timer are retired; the
metadata privacy trigger remains in place.

### ChatGPT subscription routes

The model list exposes `chatgpt-sol-latest`, `chatgpt-terra-latest`, and
`chatgpt-luna-latest`. They currently route to `chatgpt/gpt-6.1-sol`,
`chatgpt/gpt-6.1-terra`, and `chatgpt/gpt-6-luna`. These are HomeCompute aliases;
review the tracked model registry when updating their upstream targets.
LiteLLM's ChatGPT provider needs a gateway-owned subscription login. One OAuth
account is shared by every virtual key allowed to call these aliases; LiteLLM
does not select a different ChatGPT account per key.

Do not use **Models & Endpoints → LLM Credentials → Add Credential** for this
login. That form saves provider credential values; the ChatGPT subscription
login is a device-flow OAuth file. After deploying the Compose mount and
directory, sign in from an interactive shell on `home-core`:

```bash
sudo docker exec -it --user 101:101 homecompute-control-plane-litellm-1 \
  python3 -c 'from litellm.llms.chatgpt.authenticator import Authenticator; Authenticator().get_access_token()'
```

Complete the displayed device-code flow in your browser. Do not paste the code
or tokens into chat, shell history, or Git. LiteLLM writes the refreshable
credential to `/srv/state/control-plane/chatgpt-auth/auth.json`, mounted at
`/run/homecompute/chatgpt` and owned by container UID 101. The file must remain
writable because LiteLLM rotates refresh tokens in place. After sign-in, restart
LiteLLM so the configured deployments initialize with the credential:

```bash
sudo docker compose --env-file /etc/homecompute/control-plane.env \
  -f /srv/homecompute/current/deploy/control-plane/compose.yaml restart litellm
```

Verify only file metadata and a successful synthetic request; never print the
JSON contents. Keep the directory private and include it in the protected
backup/restore plan for `/srv/state/control-plane`.

Give each client a separate virtual key restricted to only the aliases it needs,
with no MCP grants unless separately approved. Subscription requests have no
per-token API charge in LiteLLM; use request/token rate limits instead of a
dollar budget. There is no automatic API-key fallback.

## Central MCP gateway

LiteLLM also owns the private MCP gateway on `https://mcp.home.arpa/mcp`. Keep
server definitions in `litellm-config.yaml`. The catalog contains the local
read-only Aula and TilbudsTrolden services, public Context7 documentation, and
the owner-only n8n instance MCP. It also has three aliases for the private
HomeCompute capability relay: Hermes owner, OpenClaw agent, and Android-only
read access. Those aliases keep the existing per-principal bearer credentials;
clients send them as server-specific `x-mcp-<server-alias>-authorization`
headers alongside a separate LiteLLM key. The relay accepts LiteLLM only from
its fixed source address and `/mcp` path. LiteLLM trusts the relay using the
read-only Home Core CA certificate. Context7 and n8n share the isolated internal
client network with LiteLLM; the two local MCP backends remain on the separate
`mcp-backends` network. n8n's instance MCP token is mounted from the SOPS-managed
`control-plane/n8n-mcp-token` secret. Existing direct clients stay in place
until their individual grants and workflows are migrated and verified.

LiteLLM's Admin UI manages MCP access on this private host. The deployment marks
existing unscoped virtual keys `no-mcp-servers` before Caddy exposes the route;
programmatically generated service keys use the same deny sentinel. Give each
client a separate virtual key, assign only its approved MCP access group, and
remove unneeded tools in that key's settings. Servers are configured with
`allow_all_keys: false`; `n8n-owner` grants the reviewed instance catalog,
while `docs-public` grants only Context7. Each HomeCompute relay alias has a
distinct access group, and its client must present that principal's existing
bearer. Do not grant Aula, `n8n-owner`, or Hermes-owner access to OpenBot's
shared agent list by default. The Android alias is explicitly allowlisted to
five read tools and five bounded UI interaction tools; grant its group only
after the connection test verifies that exact ten-tool inventory. For keys
created in the Admin UI, select
No MCP Servers unless you are granting reviewed access. The UI stores virtual
keys and grants in the existing
LiteLLM database; the release config remains the source for the server catalog.
For setup and the client migration plan, see [ADR-033](../../docs/adr/033-litellm-mcp-gateway.md)
and the [gateway evaluation](../../docs/research/mcp-gateway-evaluation-2026-11-10.md).

The `mcp.home.arpa` private DNS A record must point to `192.168.30.122` in the
home LAN resolver. Use `/mcp` for MCP clients and `/ui/` for the LiteLLM Admin
UI; `/mcp` is a protocol endpoint, so opening it in a browser without a client
key returns an authentication challenge. The Homepage administration card
opens the Admin UI.

Speech clients use the [speech API contract](../../docs/speech-api.md).
`whisper` serves transcription. `tts-danish` and `tts-english` are stable
capability aliases for the selected bilingual OmniVoice service; `omnivoice`
remains available for existing clients. Plapre is retired. The dedicated
[OmniVoice lifecycle](../omnivoice/README.md) owns speech adapters; model and
voice qualification remain separate from successful API checks.

Caddy remains in this project while it fronts only this gateway. The official
image starts as root, but it listens on unprivileged container port 8443, has
only its binary-required `NET_BIND_SERVICE` capability, has a read-only root filesystem, and can write
only its named data/config volumes. A Docker socket, host network, privileged
mode, devices, and broad host bind mounts are absent. LiteLLM uses the signed
upstream non-root image family. PostgreSQL starts directly as Alpine UID/GID 70 with all capabilities removed.
Its state directory must be owned by 70:70; NixOS tmpfiles provisions this.
Starting directly preserves supplementary group 989 for reading SOPS secrets;
the official root entrypoint would discard that group when switching users.

The Caddy edge also serves the separately managed PodWave application at
`https://podwave.home.arpa/`. PodWave does not join the control-plane internal
networks. Its dedicated bridge is shared only with Caddy, and its second bridge
allows outbound provider requests. Caddy requires a generated Basic Auth login
before forwarding any route; the password and hash are stored under
`/srv/state/podwave-auth`, outside Git and outside the app container. See the
[PodWave runbook](../podwave/README.md) for setup, DNS, provider configuration,
backup, and recovery.

## Network and transport policy

Only Caddy publishes a host port in the default profile: TCP 443 on the exact
IPv4 addresses in the control-plane environment. The optional speech profile
adds only the two Home Assistant-restricted Wyoming ports described below.
There is no TCP 80 or UDP 443. Wildcard IPv4 and IPv6 publications are
forbidden.

- `edge` contains Caddy and LiteLLM. It is the sole egress-capable application
  bridge; LiteLLM is given its default route there.
- `clients` is an isolated internal bridge shared by Caddy, LiteLLM, and Open
  WebUI. It carries gateway and browser traffic without an outbound route.
  Open WebUI publishes no host ports.
- `state` contains LiteLLM and PostgreSQL, is `internal`, and uses Docker's
  isolated gateway mode. PostgreSQL has no host publication. Caddy cannot join
  this network.
- LiteLLM listens on `0.0.0.0:4000` only inside its container namespace. This
  narrow exception is necessary for Caddy to reach it and is not a host bind.

The approved LAN/Tailscale inference API is keyless. Caddy supplies a restricted
virtual key when a client sends no credentials or an unrecognized/expired key;
valid explicit client keys retain their scopes. Credential checks use a GET
probe so inference bodies remain untouched. Provision the key with
`sudo python3 scripts/setup-network-inference.py` after LiteLLM is healthy and
before starting the new Caddy entrypoint. Full host deployment does this in that
order. The root-owned 0600 key lives at
`/srv/state/control-plane/caddy-data/network-inference-key`; back it up with the
LiteLLM database. Missing/empty state prevents Caddy from starting. Browser CORS
allows all origins without cookies and handles OPTIONS on the API base and
exposed model-listing/inference paths, including bare checks and trailing
slashes. It includes private-network approval for clients that request it.
Actual request methods remain restricted; management routes remain unexposed.

The opt-in Daythread long-recording Whisper path uses the separate
`/recording-stt/v1/audio/transcriptions` route. Caddy accepts it only from the
Daythread backend address, limits the request to 40 MB, and proxies over the
existing Spark 18007 forward with a 30-minute response-header deadline. The
Spark relay requires the existing Whisper role key, validates complete mono
16 kHz PCM16 WAV up to 1,200 seconds, and passes the original multipart body
and native provenance headers through. The ordinary `/v1/audio/transcriptions`
route keeps its existing 32 MB gateway and 60-second relay bounds. The host
firewall permits only the Caddy edge and existing LiteLLM edge to reach the
18007 tunnel listener; the deployed SSH forwarding account must also allow the
existing remote target `127.0.0.1:8007` before this source route can be enabled.

`https://home-core.tail479ad.ts.net:8443/v1` uses a public Let's Encrypt
certificate for phone/browser clients. `ai.home.arpa` retains the private CA.
Before Caddy startup, run `sudo python3 scripts/renew-gateway-certificate.py --no-restart`;
normal host deployment does this automatically. The Nix-owned
`homecompute-gateway-certificate.timer` renews the certificate daily as needed
and restarts Caddy only for a changed certificate or a pending retry. Certificate
files stay in the existing `/data` mount; the host Tailscale socket is not mounted.

These networks are coarse membership controls, not per-port or egress policy.
The installer requires Docker Engine 28+ and Compose 2.33.1+. It also sets the
daemon-wide default bind for future user-defined bridge publications to
`127.0.0.1` as a fail-safe.

Start on `127.0.0.1`. The production Tailscale publication is paired with the
repository-owned `HC-CADDY-INGRESS` policy in
`modules/nixos/automation-network.nix`: Docker's original destination
`100.110.248.102:8443` is accepted only from `tailscale0` and rejected from every
other forwarded ingress path. Rebuild NixOS and verify both allowed and denied
clients whenever the address or interface changes.

LiteLLM-to-compute traffic is plain HTTP only on the dedicated, non-routed
point-to-point link. The versioned `HC-COMPUTE` policy permits the fixed
LiteLLM container address to six OpenAI-compatible compute ports. Two separate,
fixed relay addresses may reach only Plapre Wyoming port 10201 and Hviske
Wyoming port 10301; every other forwarded flow is rejected. Host and container-forwarded traffic
to the compute subnet is rejected unless it leaves the dedicated interface,
preventing bearer credentials from falling back through the LAN default route
while the cable is down. A routed
replacement must use a compute certificate trusted by LiteLLM or a mutually
authenticated tunnel.

### Opt-in SSH fallback while the private NIC is down

The dedicated `enp45s0` link remains the preferred production transport. When
that physical path is unavailable, `modules/nixos/compute-ssh-tunnel.nix`
provides a deliberately opt-in fallback over the trusted management network.
It forwards only these `home-spark` loopback listeners:

| Edge client | Host-side tunnel endpoint | Remote loopback destination |
| --- | --- | --- |
| LiteLLM `172.28.200.3` | `172.28.200.1:18003` | `127.0.0.1:8003` (Hviske HTTP transcription relay) |
| LiteLLM `172.28.200.3` | `172.28.200.1:18005` | `127.0.0.1:18300` (Flash-Next behind the `automation-moe` alias) |
| LiteLLM `172.28.200.3` | `172.28.200.1:18007` | `127.0.0.1:8007` (Whisper Turbo transcription relay) |
| LiteLLM `172.28.200.3` | `172.28.200.1:18006` | `127.0.0.1:8006` (Home Assistant fast model) |
| Plapre relay `172.28.200.4` | `172.28.200.1:18201` | `127.0.0.1:10201` |
| Hviske relay `172.28.200.5` | `172.28.200.1:18301` | `127.0.0.1:10301` |

No primary text, embedding, vision, legacy STT, or legacy TTS port is carried
by this fallback. The host INPUT chain permits each local port only from its
named container address and rejects every other source. SSH uses
`BatchMode`, `ExitOnForwardFailure`, strict pinned-host-key checking, and a
15-second server-alive interval. It restarts persistently but never falls back
to passwords, an agent, or an unpinned host key.

Provision the credentials without copying a private key into Git or the Nix
store:

1. On `home-core`, create `/etc/homecompute/compute-tunnel` as root mode `0700`.
   Generate a dedicated Ed25519 key at
   `/etc/homecompute/compute-tunnel/id_ed25519`; the private key must be
   `root:root` mode `0400`.
2. At the `home-spark` console, create the unprivileged
   `homecompute-tunnel` account. Add only the generated public key to its
   `authorized_keys`, prefixed with:

   ```text
   restrict,port-forwarding,permitopen="127.0.0.1:8005",permitopen="127.0.0.1:8006",permitopen="127.0.0.1:10201",permitopen="127.0.0.1:10301"
   ```

   The `home-spark` host firewall must admit SSH for this account only from
   `192.168.30.122`; no inference listener is moved off loopback.
3. Record the `home-spark` host key for `192.168.30.126` in
   `/etc/homecompute/compute-tunnel/known_hosts`. Verify its fingerprint at the
   physical console before trusting it. Keep the file root-owned and not
   group/world writable.
4. Set `homecompute.computeSshTunnel.enable = true` in the `home-core` host
   configuration, build, inspect the diff, and switch. This single option
   starts the persistent service and rewrites only the automation, Home
   Assistant, Plapre, and Hviske upstreams in
   `/etc/homecompute/control-plane.env` to the host-side ports above. The
   checked-in environment template remains on
   `COMPUTE_TRANSPORT=dedicated-link`.
5. Recreate LiteLLM and, when ready, the optional speech relays using the
   regenerated environment file. Verify positive access from `.3`, `.4`, and
   `.5`, and negative access from every other edge container before promoting
   any route.

To return to the preferred cable, stop the speech relays, set the option back
to `false`, rebuild, and recreate the affected containers. Confirm direct
`10.77.10.10` health before removing the dedicated tunnel account or key. Do
not run both transports under the same semantic route during cutover.

## Live deployment

The K15 deployment is recorded in [control-plane-deployment.md](../../docs/control-plane-deployment.md).
Its gateway uses `home-core.tail479ad.ts.net` on the Tailscale address, and
`/etc/homecompute/control-plane.env` holds the resolved production settings.
Backups remain deferred by the operator. The restricted SSH transport is live
for `automation-moe`, `home-fast`, Plapre, and Hviske. It forwards the
`automation-moe` alias to Qwen3.8 Flash-Next on home-spark loopback port 18300;
the listener is not exposed on the LAN. The Qwen3.8-27B endpoint remains
stopped.

The inference catalog uses local backends plus the explicit Codex ChatGPT
subscription routes described above. `auto` is initially another served name
for the single resident workhorse, not a prompt classifier. The proxy holds no
cloud API credential and does not perform paid API fallback. Codex keeps its
ChatGPT OAuth session on the client; use a dedicated model-scoped key and make
any private-data disclosure to the selected subscription model explicit.

### On-demand automation standby

The optional `automation-backup` profile runs the pinned Unsloth
Qwen3.6-35B-A3B `UD-Q4_K_M` GGUF with CPU-only llama.cpp on `home-core`.
It has no published port and shares only an isolated internal network with
LiteLLM. Both deployments belong to the `automation` model group. The normal
gateway config prefers Spark; `maintenance-start` warms and tests home-core,
then briefly recreates only LiteLLM with an alternate config that explicitly
prefers the CPU standby. This avoids waiting for a dead-but-connected Spark
tunnel during a planned swap.

The model and image stay cached, but the process is stopped during normal
operation so it does not reserve roughly 28 GiB of memory. Before a planned
Spark model swap, run:

```bash
sudo /srv/homecompute/current/scripts/setup-home-core-automation-backup.sh prepare
sudo /srv/homecompute/current/scripts/setup-home-core-automation-backup.sh maintenance-start
```

Confirm no automation request is active before running `maintenance-start`,
because the gateway recreation can interrupt an in-flight model request. Do
not stop the Spark endpoint until the command reports that its direct Danish
content/tool-call smokes and the stable `automation` alias smoke all passed.
Perform the swap, then verify the new Spark endpoint. Restore normal routing
and release the CPU model's RAM afterward:

```bash
sudo /srv/homecompute/current/scripts/setup-home-core-automation-backup.sh maintenance-stop
```

When stopped, this is a cold cached recovery path rather than instant failover.
An unexpected Spark outage can still produce failures until an operator starts
the standby. Never retry an entire n8n workflow after it may have performed a
write; retry only the model step, and keep write steps idempotent.

Live measurements on 2026-09-26: the 22,134,528,992-byte GGUF loaded in about
11–16 seconds, used about 12.7 GiB RSS, passed direct Danish/tool-call smokes
and a LiteLLM maintenance-route smoke, then stopped cleanly. `home-core` had
about 39 GiB available RAM afterward. This standby protects `automation`, not
the currently deployed n8n alias `automation-moe`; that alias mismatch is an
open availability gap.

The `assistant-canary` alias routes to the live 64K `automation-moe` endpoint
for the synthetic Hermes pilot. Give Hermes its own LiteLLM virtual key limited
to `assistant-canary`; never reuse the n8n key or the LiteLLM administrative
key. Promotion to the stable `assistant` alias requires the Hermes streaming,
tool-call, mixed-load, outage, and rollback gates.

## Installation

```bash
sudo nixos-rebuild build --flake .#home-core
sudo nixos-rebuild switch --flake .#home-core
sudo docker compose --env-file config/control-plane.env.example \
  -f deploy/control-plane/compose.yaml config --quiet
sudo docker compose --env-file config/control-plane.env.example \
  -f deploy/control-plane/compose.yaml up -d
```

The optional `speech-proxies` profile stages the two unauthenticated Wyoming
streams on the LAN-facing `home-core` address. It is deliberately absent from
normal deployment. Start it only after the private cable, both compute services,
the `HC-COMPUTE-V2` policy, and the Home Assistant-only ingress rules are
healthy:

```bash
sudo docker compose --profile speech-proxies \
  --env-file /etc/homecompute/control-plane.env \
  -f /srv/homecompute/current/deploy/control-plane/compose.yaml \
  up -d --build --wait plapre-wyoming-proxy plapre-openai-tts hviske-wyoming-proxy
```

The intended Home Assistant endpoints are `192.168.30.122:10201` for Plapre
TTS and `192.168.30.122:10301` for Hviske STT. The relays parse and log no
speech payloads, fail health checks when their upstream is unavailable, and
are restricted at the Docker ingress boundary to the Home Assistant appliance
at `192.168.30.30`. Piper `:10200` and Faster Whisper `:10300` remain the
rollback services.

The runtime environment file contains only deployment settings and paths.
sops-nix creates the root-owned secret files with group `homecompute-secrets`
and mode `0440`; the fixed group ID is passed to the non-root LiteLLM container.

Use fixed semantic-version tags plus reviewed manifest digests. The validator
allow-lists Docker Official Caddy/PostgreSQL repositories and LiteLLM's signed
GHCR non-root repository; moving `latest` or `main-stable` tags are rejected.
The deployment manifest records the rendered Compose hash, every mounted
artifact hash, runtime versions, requested image references, and actual
container image IDs.

## TLS, keys, and state

### Token usage reporting retirement

The aggregate token usage dashboard and its refresh timer are no longer hosted.
The `/usage/` Caddy route is removed, and Homepage has no token usage link.
LiteLLM's existing database privacy trigger remains installed: it removes
prompt, response, request-body, and free-form metadata fields from usage rows.
The trigger is independent of the retired dashboard and preserves the
control-plane's content-retention policy.

`ai.home.arpa` uses Caddy's internal CA. The smoke test reads that CA and uses
normal certificate validation; it never uses `curl --insecure`. Install the
root only on approved clients. The online CA private key is in `caddy-data`, so
compromise can mint trusted certificates; encrypt its off-host backup and
limit client trust to devices that need this service.

Caddy exposes approved `/v1/*` inference routes and `/healthz`. It intentionally
returns 404 for the LiteLLM admin UI and key-management API on the AI gateway
host. It also serves the separately named `chat.home.arpa` and
`models.home.arpa` virtual hosts; the latter serves only the Spark model
controls. Open WebUI remains on `clients`. Hermes and its dedicated
dashboard listeners were retired on 2026-10-27. These virtual hosts use
Caddy's internal CA. Install that CA on approved clients
before opening those pages.

Provision and revoke virtual keys from an administrator shell inside LiteLLM's
container namespace; do not distribute the master key to clients or publish the
management route for convenience. Open WebUI receives its own revocable
LiteLLM virtual key. `/srv/state/open-webui/data` contains its database,
conversation history, and uploads; the state directory is provisioned by NixOS
but still needs an encrypted off-host backup and restore procedure. The
deployment script leaves Open WebUI stopped until both runtime key files exist.

The Model Manager application and its dedicated SSH bridge are no longer part
of the home-core release. The Spark model controls at `models.home.arpa/control/`
remain served by their separate lifecycle.
The `embedding` alias now routes to Harrier 0.6B on home-core through the private
clients network. Its OpenAI-compatible `/v1/embeddings` endpoint uses the same
gateway admission, optional credentials, and CORS policy as the other APIs.
The network key scope is expanded explicitly with
`sudo python3 scripts/setup-network-inference.py --enable-embedding`.
Vision, English STT, and TTS candidates are also absent from the production
LiteLLM model list and Caddy route allow-list while staged. Qualify one modality
at a time against its direct private listener; add a gateway alias only through
an explicit promotion change with route, quota, monitoring, and rollback tests.

Back up and restore-test `/srv/state/control-plane` together with the persistent
sops age identity before production use. Never change the LiteLLM salt after
virtual keys exist. The NixOS backup module remains disabled until its real
off-host repository and runtime password path are configured. Image updates
are manual and require backup plus release-note and vulnerability review; no
automatic container updater is installed.

### Spark transcription over the HTTPS gateway

The replacement target is now full Whisper Large-v3 for every STT alias.
Use the [Whisper lifecycle](../whisper-stt/README.md) and
[ADR-027](../../docs/adr/027-unified-whisper-large-v3-stt.md). The older split
deployment instructions below are historical rollback context, not the desired
activation path. The existing core Wyoming service name and tunnels remain
compatible with the new single relay.

The network model `stt-danish` routes `/v1/audio/transcriptions` to the running
Spark Hviske v5.3 GPU service. Run `scripts/setup-compute-hviske-stt.sh http-up` to build/start only the
logging-free `hviske-http` profile; it publishes `127.0.0.1:8003` and needs no
primary model restart. Its isolated Python/FFmpeg image converts phone audio
to PCM WAV, with 10 MB/60 second/4 concurrent request bounds. The dedicated forwarding-only SSH account must allow
`127.0.0.1:8003`. Home-core maps it to `172.28.200.1:18003`, admitting only
LiteLLM at `172.28.200.3`. Set `COMPUTE_STT_DANISH_BASE_URL` to that `/v1` URL.
When migrating an existing reviewed text-only network key, run
`scripts/setup-network-inference.py --enable-stt` on home-core after adding the
model; ordinary provisioning verifies scope without widening existing keys.
The common HTTPS base works without client credentials and permits browser
preflight. The `whisper-turbo` alias serves the separate pinned Spark Whisper Large-v3
Turbo container through `172.28.200.1:18007` to `127.0.0.1:8007`, admitting
only LiteLLM. Use `COMPUTE_STT_MULTILINGUAL_BASE_URL` for that `/v1` URL.
Migrate an existing reviewed Danish+text network key with
`scripts/setup-network-inference.py --enable-whisper`.

The isolated `deploy/whisper-stt/compose.yaml` uses the existing compute runtime
identity and shared upstream key paths from `/etc/gb10-ai/hviske.env`. Run its
`whisper-fetch` and `whisper-accept` services with the `prepare` profile before
starting `whisper-primary` and `whisper-http` with `up -d --build --wait`.
Acceptance verifies the pinned upstream weight SHA-256 and records a private
manifest; startup verifies it offline. Only the relay publishes a loopback port.
The forwarding-only Spark account must permit `127.0.0.1:8007`.
