# Orca on Home Core

[Orca upstream v1.4.268](https://github.com/stablyai/orca/releases/tag/v1.4.268)
runs its headless Node server under Docker. The image includes the browser
renderer from the matching, SHA-256-verified Debian package. Neither Electron
nor a desktop session is needed. The root path and `/web-index.html` both serve
the real client. Caddy terminates HTTPS and proxies Orca's authenticated RPC
WebSocket. The container listens on its isolated bridge and publishes no host
port. See [upstream evidence](../../docs/research/orca-upstream-2026-11-21.md)
for the release commit and packaging details.

The only writable host mount is `/srv/state/orca`. It contains the private Orca
profile, bot memory and chat history, workspace trees and provider account data.
The container runs as UID/GID 12074 with a read-only root, three CPUs, 3 GiB
RAM, 384 processes, a private tmpfs and no Linux capabilities. The ingress
relay separates the runtime's bridge from the shared edge. The runtime has no
direct network route to Home Core services, Spark or the internet. It can
reach the model relay and a TLS CONNECT proxy restricted to `github.com` and
`api.github.com` by default. The model relay holds only a revocable
`automation-moe` LiteLLM key with MCP denied. It forwards only model listing
and Chat Completions to the Caddy front door at `https://llm.home.arpa/v1`,
validating the internal CA and server name. Photos, recordings, household
data, deployment credentials and private repositories are outside this pilot.
Project groups use separate bot memory and worktrees. Every agent runs under
the same container UID, so the runtime is suitable for an owner-trusted
pilot with public repositories and synthetic data.

Docker starts the services automatically on boot and restarts failed
processes. Orca's supervisor restarts its server child. The runtime health
check loads the bundled web entry point, while the relays check their local
listeners. Model inference is independent of runtime startup.

## Release and DNS

Deploy the exact reviewed commit from `origin/master`:

```sh
./scripts/homecompute deploy home-core --revision FULL_SHA
./scripts/homecompute status
./scripts/homecompute services
./scripts/homecompute doctor
./scripts/homecompute drift
```

The wrapper builds the verified image, brings up the Caddy and Homepage
release, provisions the model-only key through the existing LiteLLM key
lifecycle, starts Orca and verifies the client entry point. Homepage includes
**Orca** under **AI assistants**.

In UniFi Network, open **Settings → Policy Engine → DNS → Create DNS Record**
(or **Policy Table → Create New Policy → DNS** on versions with that menu).
Create the **Host (A)** record from [the declaration](../../config/orca-dns.json):
`orca.home.arpa` → `192.168.30.122`, TTL 300 seconds, in the existing private
DNS environment. The address was confirmed on the Home Core LAN interface.
Use the existing `home.arpa` Tailscale split DNS and subnet route for remote
clients. [UniFi DNS procedure](https://help.ui.com/hc/en-us/articles/15179064940439-UniFi-DNS-Records-and-Local-Hostnames)

Verify DNS and TLS from an approved device, and check that an unapproved VLAN
or origin cannot access the site:

```sh
dig +short orca.home.arpa
curl --cacert /path/to/home-core-root.crt -fsS https://orca.home.arpa/ -o /dev/null
curl --cacert /path/to/home-core-root.crt -sS -o /dev/null -w '%{http_code}\n' \
  -H 'Origin: https://foreign.invalid' https://orca.home.arpa/
```

The final request should return HTTP 403. Before DNS is applied, add
`--resolve orca.home.arpa:443:192.168.30.122` to the curl commands to test TLS
and routing independently of DNS. Trust the HomeCompute CA on each approved
device before opening [Orca](https://orca.home.arpa/). Check client-specific CA
trust when using the mobile app.

## Pair clients

On an owner terminal, generate a fresh link and open the browser link or paste
the desktop link into **Settings → Remote Orca Servers → Add Server**:

```sh
ssh -t home-core 'sudo docker exec -it homecompute-orca-orca-1 orca pair --address wss://orca.home.arpa --rotate'
```

For Orca Mobile, add `--mobile` and scan the displayed code. Use a separate
offer per device. Keep links private. The browser uses a URL fragment for
the credential. Startup runs with `--no-pairing`, and the container log
driver is disabled. Pairing the browser and loading the workspace inventory
exercise the real encrypted WebSocket handshake. Keep its browser storage.
For revocation in this headless version, stop Orca and reset its pairing
identity in the same state mount. This invalidates all device grants:

```sh
revision=FULL_SHA
release=/srv/homecompute/releases/$revision
sudo env ORCA_RELEASE="$revision" docker compose -f "$release/deploy/orca/compose.yaml" stop orca
sudo env ORCA_RELEASE="$revision" docker compose -f "$release/deploy/orca/compose.yaml" \
  run --rm --no-deps orca orca serve reset-identity --yes
sudo env ORCA_RELEASE="$revision" docker compose -f "$release/deploy/orca/compose.yaml" start orca
```

## Teams, providers and GitHub

| Group | PO bot | GitHub checkout | Default agent |
| --- | --- | --- | --- |
| HomeCompute | HomeCompute PO | `madslundt/HomeCompute` · `master` | OpenCode 2.3.2 · `automation-moe` |
| ChessMate | ChessMate PO | `madslundt/ChessMate` · `main` | OpenCode 2.3.2 · `automation-moe` |
| Daythread | Daythread PO | `madslundt/Daythread` · `main` | OpenCode 2.3.2 · `automation-moe` |

The intended state lives in [teams.json](teams.json) and the
[PO instructions](instructions/product-owner.md). Add a project there with its
verified repo, base branch, context and review time. Use an owner-paired Orca
browser to apply the manifest. The helper uses the authenticated RPC bridge
shipped in the pinned browser client, with no database writes. Verify the
method contract before bumping Orca. It keeps existing bot memory and refuses
to replace a Group with different membership or a project with a different
remote. Run once without `--apply` to inspect, then use a newly minted offer for
the approved apply. Use a trusted Chrome installation, Node and Playwright on
the workstation with the HomeCompute CA installed. Pairing links must never
enter command arguments, shell history or logs.

```sh
orca_tools=$(mktemp -d)
npm install --prefix "$orca_tools" --no-fund --no-audit playwright@1.58.2
export ORCA_PLAYWRIGHT_MODULE="$orca_tools/node_modules/playwright"
ssh home-core 'sudo docker exec homecompute-orca-orca-1 orca pair --address wss://orca.home.arpa --rotate --json' \
  | jq -er '.result.webClientUrl' \
  | node scripts/reconcile-orca-teams.mjs --apply
```

Each PO owns its project Group. Lead Engineer, Engineer, Researcher, UX
Designer, UI Designer and QA Engineer are reusable roles applied to native,
task-scoped workers. OpenCode uses chat completions, tool calls and thinking
with `reasoning_effort=xhigh`. [OpenCode provider configuration](opencode.json)
holds the exact wire settings. The team installer selects native Chat and Ask
permission on the pilot host. Keep worker specs to the project, issue,
acceptance criteria and the one assigned role. Launch coding in a separate
Git worktree and give QA independent review input:

```sh
orca orchestration worker-start --agent opencode2 --worktree new-top-level \
  --repo id:REPO_ID --setup skip --spec-file /data/projects/worker-spec.json
orca orchestration worker-show WORKER_ID
orca orchestration worker-read WORKER_ID --source transcript
```

Run `orca orchestration worker-start --help` from the PO's own Orca chat
terminal context to see the required objective, task and run arguments. Use
the native worktree and orchestration identities in the handoff. Shared
persistent Orca bots retain the same memory across Groups, so the shared
roles here instantiate as workers per task and the POs hold durable project
memory. The bot folders hold Orca's SOUL and MEMORY files.

Public repository cloning and public issue, PR and check-run reads work
without a GitHub token. Schedule creation uses native Orca automations and
starts disabled so the owner can review the first result. The schedules run
on weekdays at 09:00, 09:15 and 09:30 Copenhagen time and post to bot chat.
Use the read-only GitHub helper to verify each repo, then enable the schedules:

```sh
sudo docker exec homecompute-orca-orca-1 orca-reconcile-schedules
sudo docker exec homecompute-orca-orca-1 orca-project-status homecompute
sudo docker exec homecompute-orca-orca-1 orca-project-status chessmate
sudo docker exec homecompute-orca-orca-1 orca-project-status daythread
sudo docker exec homecompute-orca-orca-1 orca-reconcile-schedules --enable
sudo docker exec homecompute-orca-orca-1 orca automations list
```

For GitHub writes, the PO drafts an issue or PR and gives the owner the
worktree, commits, test results and linked issue. Publish after approval with
the operator's existing GitHub workflow or a separately issued, repo-scoped
credential. Keep write credentials out of the shared Orca runtime. GitHub
webhooks and source-thread replies use Orca Cloud with an installed GitHub
App. Relevant GitHub event, actor, repository and issue/PR content passes
through Orca Cloud. That opt-in connector, native cloud relay and CI event
triggers stay disabled. A CI-triggered experiment should start with a
read-only summary and request approval before changing code.

Codex 0.171.0 is installed as an optional provider. To opt in on Home Core,
run `sudo bash /srv/homecompute/current/scripts/set-orca-codex-egress.sh enable`.
Then sign in with a dedicated approved account from your owner terminal:

```sh
ssh -t home-core 'sudo docker exec -it homecompute-orca-orca-1 orca account add --agent codex'
```

The network selection is retained as an owner-only receipt and can be turned
off with the same installer and `disable`. Verify sign-in and a synthetic
manual request before assigning Codex to Lead Engineer. Keep schedules and
routine work on the local model. Cloud prompts and files go to the chosen
provider. Select it explicitly for each approved task and use only cleared
data. Claude Code needs its own install and sign-in in this trust boundary.
Telemetric reporting is disabled in the pilot, and Orca Cloud sign-in and
connections are unconfigured.

## Backup, upgrade and rollback

`homecompute-orca-backup.timer` runs a cold backup at 02:30 Copenhagen time
before the existing NAS archive window. It stops only Orca, retains its
initial running state, and writes a root-only archive and checksum under
`/srv/backups/agent-recovery/orca`. The encrypted NAS job takes completed files
from that directory via its existing source tree. Check the backup unit,
the checksum and the encrypted snapshot before relying on restoration:

```sh
sudo systemctl start homecompute-orca-backup.service
sudo systemctl status homecompute-orca-backup.service homecompute-orca-backup.timer
sudo ls -l /srv/backups/agent-recovery/orca
```

The shared deploy lock serializes upgrades and backups. Before an upgrade,
verify the current backup, drain worktrees with running tasks and deploy the
new reviewed full SHA. Upgrades take a pre-change cold backup as well. For
a restore drill, extract a checksum-verified archive into a fresh private
directory under `/srv/restore-tests` with numeric owners. Inspect the restored
profile, bots and Git worktrees in an isolated disposable runtime and keep
its network off. Never start two runtimes with the same pairing identity on
reachable networks. Off-host restores may require device and provider
re-pairing. The model key is separate and can be reprovisioned through the
release lifecycle.

If rolling back an upgrade, stop Orca and keep the current state directory
as a recovery preimage. Restore the last compatible backup into a new
`/srv/state/orca` with ownership 12074:12074 and mode 0700, and deploy the
prior Orca release using the usual wrapper. Before returning to a release
that predates Orca, take a backup and use its active release to remove just
the Orca Compose stack, then deploy the previous release:

```sh
revision=ACTIVE_ORCA_FULL_SHA
sudo env ORCA_RELEASE="$revision" docker compose \
  -f "/srv/homecompute/releases/$revision/deploy/orca/compose.yaml" down
./scripts/homecompute deploy home-core --revision PREVIOUS_FULL_SHA
```

Keep the data and archives for recovery. Remove the DNS record when retiring
the service, revoke the dedicated LiteLLM key if retiring permanently, and
remove any intentionally enabled Codex access through its owner workflow.
