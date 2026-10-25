# K15 gateway deployment

Deployed 2026-09-04 on `home-core` (`192.168.30.122`, Tailscale
`100.110.248.102`). Approved package/image maintenance on 2026-10-10 updated
the services below; database restores and migration rollback startup were rehearsed.

| Service | Version | Status |
| --- | --- | --- |
| Caddy | 2.11.7 Alpine | Private TLS gateway |
| LiteLLM | 1.104.2 non-root | Authenticated API and virtual-key database |
| PostgreSQL | 16.15 Alpine | Dedicated LiteLLM database, no host port |
| n8n | 2.42.6 | Database migrated; workflow/credential counts and active flags preserved |
| Plapre relay | Python 3.12.15 | Healthy; Home Assistant-only LAN ingress on 10201 |
| Hviske relay | Python 3.12.15 | Healthy; Home Assistant-only LAN ingress on 10301 |
| Spark Whisper HTTP | Whisper Large-v3 Turbo + bounded relay | `whisper-turbo` through home-core HTTPS; one transcription at a time |
| Spark Hviske HTTP | Hviske v5.3 + Python/FFmpeg relay | `stt-danish` through home-core HTTPS; keyless audio transcription |
| Automation standby | Approved server digest | Qwen3.6 Q4 cached, normally stopped |

Immutable image digests are recorded in `config/control-plane.env.example`.
The update verified immutable public registry digests and platform identities;
independent cosign verification was not performed for these new pins. The original
LiteLLM 1.99.1 deployment had its separate cosign receipt.
Release references: [LiteLLM](https://github.com/BerriAI/litellm/releases),
[Caddy](https://github.com/caddyserver/caddy/releases),
[PostgreSQL image](https://hub.docker.com/_/postgres).

Metadata-only token tracking was enabled on 2026-10-21. PostgreSQL's
`homecompute_metadata_only_usage` trigger strips content and free-form metadata
before usage inserts/updates. Synthetic streaming and non-streaming requests
both recorded 95 input / 32 output tokens; all usage rows had empty content
fields. Query daily/weekly/monthly totals on home-core with
`sudo /usr/local/bin/homecompute-token-usage --period day|week|month`, optionally
adding `--by-model`, `--since`, `--until`, or `--json`. See the
[reporting details](../deploy/control-plane/README.md#metadata-only-token-usage).
The initial targeted rollout used
`/var/lib/homecompute/token-usage/compose.override.json` and a copy of the
existing config with only spend logging enabled; the immutable release checkout
was preserved. Docker retains this mount across restarts. A full deployment of
the updated source installs the same database guard and reporter and returns
LiteLLM to the release-owned config. Redeploying an older source revision can
disable collection again; history remains in PostgreSQL.

The token dashboard is live at `https://ai.home.arpa/usage/` and
`https://home-core.tail479ad.ts.net:8443/usage/`, with a **Token usage** entry on
Homepage. The exact static route and atomic JSON snapshot add no listening
port or database-facing web service. The Nix-built
`homecompute-token-usage.timer` refreshes the aggregate snapshot every minute.
For the initial targeted rollout, its GC-rooted units were linked under
`/usr/local/lib/systemd/system`; the source Nix module owns subsequent full
deployments, and the deployment script removes these temporary unit links.
Gateway and Homepage configuration overrides and rollback files are recorded
in `/var/lib/homecompute/token-usage/dashboard-deployment.json`. The immutable
release files and existing image versions were preserved.

The cache/pricing dashboard update uses the same targeted rollout: install
the updated database guard, reporter, and static assets under the shared
deployment lock, preserving the current gateway configuration. A persistent
`/etc/systemd/system.control/homecompute-token-usage.service.d/90-cache-pricing.conf` drop-in runs the
reporter from its pushed immutable release checkout. The full deployment
script removes that drop-in when returning to the Nix-owned reporter.
Deployment receipts and rollback files are kept under
`/var/lib/homecompute/token-usage`. Inference runtime cache-reporting flags
are source-controlled separately; deploying this dashboard on home-core does
not restart home-spark models or backfill missing historical cache counts.

Verification passed six Python usage/publication tests, four browser-aggregation
tests, the Nix service/timer build, and live browser checks for model filters,
weekly/monthly boundaries, empty ranges, exact period tables, and refresh.
Both HTTPS addresses return 200; unsupported paths and writes return 404.
The refresh timestamp advances and all stored usage content fields remain empty.
The gateway, Homepage, and LiteLLM are healthy.

Visual QA compared the full generated concept and the final browser screenshot
with `view_image`. Layout, typography, white/navy
palette, blue/teal token encoding, filter controls, numeric hierarchy, and table
structure follow the concept. Intentional additions are the snapshot freshness
status, exact period breakdown, and collection-coverage note; no unknown history
is plotted as zero. The title, subtitle, labels, and action copy match. A chart
resize jump and a missing Homepage log mount directory were corrected.
IAB checked the initial screen; Playwright checked desktop 1505×1045 and mobile
390×844 because IAB has no viewport resize API. Final browser checks used an SSH
loopback bridge that fetches the actual HTTPS assets with certificate validation,
because this workstation could not reach the tailnet HTTPS listener directly.
The final layout was visually verified against the concept with no remaining
material visual mismatch; mobile tables retain horizontal scrolling for detail.

## Access

Phone/browser API base: `https://home-core.tail479ad.ts.net:8443/v1`, with a
publicly trusted Let's Encrypt certificate provisioned by Tailscale. No local
CA installation is needed for this hostname. The gateway remains private.

LAN API base: `https://ai.home.arpa/v1` over the approved LAN or Tailscale subnet
route. Health endpoint: `https://ai.home.arpa/healthz`. The direct Tailscale
listener is `https://ai.home.arpa:8443/v1` when the hostname resolves to
`100.110.248.102`. Caddy publishes only the exact LAN 443 and Tailscale 8443
addresses, with source restrictions in `HC-CADDY-LAN` and `HC-CADDY-INGRESS`.
Tailscale Funnel owns port 443 for the separate TTLock webhook.

The `ai.home.arpa` certificate uses Caddy's private CA. Its public root is available
on the host at `/srv/state/control-plane/caddy-data/caddy/pki/authorities/local/root.crt`.
A local copy is in `artifacts/home-core-root.crt` (ignored by Git).
Configure individual clients with that CA; system-wide client trust has not
been installed. For example, from this checkout:

```bash
curl --cacert artifacts/home-core-root.crt \
  https://ai.home.arpa/healthz
```

Since 2026-10-13, approved LAN/Tailscale API clients can omit credentials.
Caddy supplies a non-admin network key scoped to `automation`, `home`,
`automation-moe`, `stt-danish`, and `whisper-turbo`, stored root-owned 0600 in
`/srv/state/control-plane/caddy-data/network-inference-key`. Explicit client keys
retain their scopes when valid. Unrecognized credentials fall back to the same
restricted network access; SDKs can use `local` as a placeholder. CORS allows any
origin with successful preflight on the exposed model-listing and inference
paths, without cookie credentials. The administrative master
key is not a client credential. Key-management endpoints and the admin UI are
not exposed through Caddy. An administrator can call `/key/generate` or
`/key/delete` at `http://127.0.0.1:4000` inside the LiteLLM container, reading its
master key from `/run/secrets/litellm_master_key`. A persistent n8n consumer
credential is now stored encrypted in n8n for the OpenAI-compatible local
endpoint. The administrative master key is still not a client credential.
The candidate Plapre speech alias is excluded from that key until an operator
explicitly runs `scripts/setup-network-inference.py --enable-plapre`.

## Secrets and operations

- The public Tailscale certificate pair lives under
  `/srv/state/control-plane/caddy-data/tailscale-tls`, with a root-only private
  key and an atomic `current` version pointer. The daily
  `homecompute-gateway-certificate.timer` checks validity using
  `scripts/renew-gateway-certificate.py`, requests at least 30 days from Tailscale,
  and restarts Caddy when the certificate changes. Bootstrap deployment
  provisions the pair before starting Caddy. The daemon socket stays on the host.
- Encrypted source: `secrets/home-core.sops.yaml`.
- Host-only age identity: `/var/lib/sops-nix/key.txt`, root-owned, mode 0600.
- sops-nix materializes five gateway secrets under `/run/secrets/control-plane`,
  root-owned, group `homecompute-secrets` (989), mode 0440.
- Production environment: `/etc/homecompute/control-plane.env`, root-owned 0600.
- Durable data: `/srv/state/control-plane/{caddy-data,caddy-config,postgres-data}`.
- PostgreSQL starts as UID/GID 70 and retains supplementary secret group 989.
  Its data directory is provisioned as owner 70:70, mode 0700.
- Caddy retains `NET_BIND_SERVICE` because the official binary's file capability
  otherwise causes an exec permission error. Other capabilities are dropped.
- Docker restart policy is `unless-stopped` for all three services.

On the host:

```bash
sudo docker compose --env-file /etc/homecompute/control-plane.env \
  -f /home/mads/HomeCompute/deploy/control-plane/compose.yaml ps
sudo docker compose --env-file /etc/homecompute/control-plane.env \
  -f /home/mads/HomeCompute/deploy/control-plane/compose.yaml up -d --wait
```

Recreate affected containers after changing SOPS secrets because Compose mounts
resolved secret files. Do not regenerate the salt or rotate database passwords
without coordinating the corresponding database changes. To stop this stack,
use the same Compose arguments followed by `stop`; its persistent data remains.

Published deployments now use `nixosConfigurations.home-core` and clean release
checkouts under `/srv/homecompute/releases`. Runtime files are generated by
NixOS. See [Git deployment](git-deployment.md) for the single-command workflow.

## Verification

- Three service health checks pass; PostgreSQL migrations completed.
- Real TLS validation from the Mac succeeds using the exported CA.
- Health returns 200; keyless `/v1/models` and `automation-moe` chat return 200.
- Browser preflight returns 204 with wildcard origin on supported API routes.
- Temporary virtual key creation succeeds; the key sees only `automation`.
- Revocation succeeds and the revoked key is rejected by internal LiteLLM;
  the public gateway falls back to the common network aliases.
- Public `/ui` and `/key/generate` return 404.
- LAN host ports 443, 4000, and 5432 are closed/unreachable.
- A disposable, network-isolated PostgreSQL instance initialized from empty
  state with the same secret permissions and a non-superuser `litellm` role.
- Container recreation preserves state and returns all three services to healthy.
- NixOS activation and the control-plane Compose assertions pass. The books
  importer now has dedicated Compose/isolation checks in repository validation.

### Client key scope and tool-call verification

Live verification on 2026-09-27 confirmed valid `required` and `auto`
`get_temperature` tool calls through the gateway for `automation-moe`,
`assistant-canary`, and `home` (Gemma 4). These checks used the administrative
verification credential. The three documented client keys
(`HOMECOMPUTE_SCRIPT_API_KEY`, `HOMECOMPUTE_API_KEY`, and
`HOMECOMPUTE_PI_API_KEY`) each list only `automation-moe` from authenticated
`/v1/models`; requesting another alias returns HTTP 403. Tool-call capability
at a backend therefore does not imply that a client key is allowed to use its
alias. The chat helper's `--model` option chooses the requested alias and does
not change key permissions. Provision a separately scoped virtual key for any
additional alias; do not broaden existing client keys by default.

The live gateway tool checks do not establish current runtime support for the
stopped Qwen3.8 primary or the stopped CPU `automation-backup` service. Their
configuration-level smokes are not a substitute for activating and qualifying
those runtimes.

## Remaining work

- n8n uses the LiteLLM `automation-moe` alias. The alias is routed through the
  restricted tunnel to the home-spark Flash-Next trial on loopback port 18300.
  The home-core CPU
  standby is registered only under `automation`, so it is not yet a fallback
  for those workflows.
- Qwen3.8-27B is stopped. The general semantic aliases that still use its
  direct `10.77.10.10:8000` endpoint are unavailable.
- Replace the SSH fallback with the dedicated private compute link and repeat
  all positive/negative network tests.
- Capture scheduled n8n production executions, mixed-load results, and
  retry/idempotency evidence after the local-model cutover.
- Complete Home Assistant Hviske and end-to-end Assist latency acceptance.
- Configure encrypted off-host backups and perform a complete restore drill.
- Rotate the LiteLLM administrative key after coordinating clients.
- Hermes/OpenShell, browser workers, and remaining HAOS migrations remain
  deferred.
