# n8n source inventory

## Cutover complete (2026-09-04)

The owner logged into the restored instance and its community-node UI was
verified. n8n is healthy at `http://home-core.tail479ad.ts.net:15678` through
Tailscale. All nine credentials were retained and verified decryptable.

The HAOS source's actual database at `/data/n8n/.n8n/database.sqlite` was
unpublished and the source add-on stopped. Its data is retained for rollback.
No unfinished executions were present; execution 223 was the last source run
and remained the last run after target activation. No migration test triggered
production side effects.

The target now runs the original published versions:

- Notion AI automations: `2fd65c7b-d517-4d3a-b067-df1e5df8d795`.
- Aula workflow: `c010dabf-64ef-4b6e-8889-903c745d3a67`.

Aula's unpublished draft differs and was deliberately not promoted. The other
workflows remain unpublished. The next scheduled run after cutover is Notion
at 21:00 Copenhagen time; a post-cutover scheduled execution has not yet been
observed. No background monitoring task has been configured.

Production uses both `compose.yaml` and `production.yaml`. Internet TLS and
Aula MCP connectivity passed; HA admin and router access were verified denied.
The egress policy is required before Docker starts. See the automation README
for the exact rollback procedure. Scheduled off-host backups remain deferred.

## Restore preparation (2026-09-04)

The owner explicitly deferred scheduled off-host backups and requested continued
deployment. The owner also requested the latest n8n. The official `latest`
image was pulled and reports 2.37.10; GitHub identifies 2.38.3 as the newest
beta. Since the source already runs 2.38.3, the target retains that version
rather than downgrading its database.

An application-consistent snapshot was captured during a brief source pause,
then the source was resumed and confirmed running/unpaused. The snapshot was
encrypted for transfer and its SHA-256 matched at the destination. SQLite's
integrity check passed. Source data includes four visible workflows, one
archived workflow, and nine credentials.

The target is deployed as `homecompute-automation-n8n-1` on home-core, with
state at `/srv/state/automation/n8n`. The pinned official image is
`docker.n8n.io/n8nio/n8n:2.38.3@sha256:4b76b9c5a69dc1c0f26bedd21b4e281ac2c84bd88d33857487b9e63dd0a42e87`.
The readiness endpoint passed and all nine credentials decrypted successfully
without printing their values. The copied target workflows were unpublished
before startup; the source workflows remain published and authoritative.
Outbound HTTPS was verified blocked on the target's internal Docker network.
An SSH tunnel presents the target editor at `http://localhost:15678` on the
operator workstation. The existing n8n login is required to inspect its UI.

At the isolated-restore stage, production cutover was pending. Those checks
were completed as recorded above. The source migration snapshot remains
under `/tmp/homecompute-n8n-transfer` on HAOS; a target copy remains in the
mode-0700 `/home/mads/homecompute-migration` directory. These are migration
rollback material, not a configured backup service.

Observed active schedules in Europe/Copenhagen:

- Notion AI automations: 07:00 and 21:00 daily.
- Aula workflow: 15:00 Monday–Friday and 09:00 Sunday.
- Aula MCP dependency: `http://homeassistant.local:7878/sse`; provide an
  explicit container DNS/hosts mapping to HAOS at `192.168.30.30` for cutover.

## Initial read-only inventory

Observed read-only on 2026-09-04 through the owner's authenticated Home
Assistant session and existing Advanced SSH & Web Terminal. No workflow,
credential, schedule, application setting, or production data was changed.

## Source

- Home Assistant: `192.168.30.30`, Core 2026.9.0, HAOS 18.2.
- n8n application: **2.38.3** (shown in n8n settings).
- Add-on: **4.4.12**, repository `https://github.com/Rbillon59/hass-n8n`.
- Container: `app_6560bdea_hass-n8n`.
- Image tag: `ghcr.io/rbillon59/hass-n8n-amd64:4.4.12`.
- Start on boot, watchdog, and automatic updates are enabled. Reconfirm the
  source version immediately before any export or restore.
- Timezone: `Europe/Copenhagen`; custom command arguments empty;
  unrestricted file writes disabled; no custom environment entries visible.
- Editor uses Home Assistant Ingress; webhook/API host port is 8081.
  External webhook URLs, callers, and OAuth callbacks remain unverified.

## Persistent state

- Container `/data` maps to
  `/mnt/data/supervisor/apps/data/6560bdea_hass-n8n` on HAOS.
- n8n state: `/data/n8n/.n8n`, approximately 49.9 MB.
- SQLite database: approximately 35.5 MB, with live WAL and SHM files.
  A raw copy of the database alone while running is not a consistent backup.
- A 56-byte `config` file exists; its contents were not printed. Preserve it
  securely and verify the credential encryption key during isolated restore.
- `nodes`, `storage`, and event logs also exist under `.n8n`.
- `/data/n8n/.cache` accounts for approximately 993.6 MB of the 1 GB total.
- Other mounts include `/share`, `/media`, `/config`, `/ssl`, and `/backup`.
  Workflow use of these paths must be checked before defining target mounts.

## Application inventory

| Workflow | Observed publication status |
| --- | --- |
| Email analyzer | No Published badge |
| Aula workflow | Published |
| Notion AI automations | Published |
| Sub-Workflow: Web Search with Fallback | No Published badge |

The Aula editor shows Friday, Monday–Thursday, and Sunday trigger nodes,
JavaScript, an MCP client, AI model nodes, and Telegram message actions.
Exact trigger times, active published revisions, credential references, and
endpoint dependencies still need capture. Do not execute it as a migration test.

Installed community packages:

- `@brave/n8n-nodes-brave-search` 1.1.8.
- `@tavily/n8n-nodes-tavily` 0.5.1.

## Recovery and remaining gates

The HA backup listing includes a protected local backup dated
2026-09-04 02:56 UTC containing this add-on. Off-host copies, recovery-key
availability, and a successful restore have not been verified.

1. Choose an off-host backup destination for home-core and retain recovery keys.
2. Capture remaining workflow dependencies, credentials metadata, published
   revisions, schedules, external callers, and binary-data configuration.
3. Preserve an application-consistent source snapshot, including the encryption
   key, database, community packages, and any workflow file dependencies.
4. Restore into an isolated target at n8n 2.38.3, with external effects blocked.
   Verify credential decryption, persistence, and backup restoration.
5. Follow `home-core-rollout-plan.md` for the per-workflow cutover. Keep source
   workflows running until their individual cutovers; never run both schedules.

Other running HA apps observed include Node-RED, Mosquitto, Zigbee2MQTT,
ESPHome, Frigate, MariaDB, InfluxDB, Grafana, Music Assistant, Piper, Aula MCP,
and Home Assistant MCP Server. Their presence is not approval to migrate them.
