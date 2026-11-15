# HomeCompute operations

The workstation CLI is `scripts/homecompute` (Python 3 standard library only).
Run it from any directory in this checkout. It uses the normal OpenSSH client
and SSH aliases `home-core` and `home-spark`; configure those hosts and pinned
host keys in `~/.ssh/config` and `~/.ssh/known_hosts` first. It enforces
`StrictHostKeyChecking=yes` and bounds connection timeouts. Read-only status uses
SSH batch mode. Deployment uses non-interactive `sudo -n` when available; when
sudo requires a password, run deployment from a terminal and enter it at the
remote sudo prompt. HomeCompute never passes a sudo password as an argument or
stores it. Status runs as the SSH user, so Docker inventory is reported as
unknown when that account cannot query Docker directly or through passwordless
sudo.

## Everyday operation

Add this checkout's `scripts` directory to your shell path once if you want the
short command name:

```bash
export PATH="/path/to/HomeCompute/scripts:$PATH"
homecompute status
```

```bash
./scripts/homecompute status
./scripts/homecompute updates
./scripts/homecompute models
./scripts/homecompute services
./scripts/homecompute doctor
./scripts/homecompute drift
```

`status --json` emits schema version 1 with `desired_revision`, observed host
snapshots, and an `unverified` map. The default desired revision is the exact
commit currently at `origin/master`. This is a read-only lookup; it does not
fetch or change the checkout. Deployments always use a full SHA. Use
`--revision SHA` to choose a reviewed historical revision or
`--from origin/master` to make the desired source explicit.

The remote snapshot reports the deployed revision file, release pointer, OS,
failed systemd units, Docker containers that actually exist, tool presence and
access, GPU visibility, the existing model update monitor report, and (on
Spark) vendor package candidates from the host's current apt metadata. A
container row is observed state; it is not inferred from Compose declarations.
Known model records come from `model-catalog.json` plus the hardware planning
roster, but the current inventory labels their live state `unknown`: there is
not yet a safe authenticated runtime query that joins live model processes to
catalog identities. `doctor` also cannot verify private artifact contents or
authenticated cross-host routes from the workstation.

Containers inside the agents VM are separate from Home Core's Docker inventory.
For the OpenBot server, use `./scripts/setup-openbot-guest.sh status` and its
[workload runbook](../deploy/openbot/README.md).

`updates` separates application/model monitor findings from DGX OS package
candidates. Apt metadata can be stale; vendor updates must be checked and
installed using NVIDIA/DGX supported maintenance procedures. The existing
weekly model monitor remains the model upstream detector. Flake and Docker
dependency proposals continue through GitHub Actions/Dependabot PRs and
repository CI; this CLI does not query GitHub PR status yet.

## Deploy the latest pushed code

Run deployment from the operator workstation checkout after committing and
pushing the changes you want to release to `origin/master`. The CLI reads the
current full commit SHA from GitHub once, then uses that same immutable
revision for every selected host. It does not deploy uncommitted workstation
files.

```bash
git status --short --branch
git push origin master
./scripts/homecompute status
./scripts/homecompute deploy all
./scripts/homecompute status
./scripts/homecompute doctor
```

`deploy all` checks both hosts before changing either, then deploys
`home-spark` followed by `home-core`. Run it from a terminal: if remote sudo
requires a password, SSH displays the prompt and you enter it there. The CLI
does not accept or store the password. Review the final status output for the
deployed revisions and failed units; `doctor` reports host readiness, but the
CLI does not yet run an authenticated cross-host route smoke test.

To deploy only one node, use `./scripts/homecompute deploy home-core` or
`./scripts/homecompute deploy home-spark`. Each command still selects the
current `origin/master` revision. For a deliberate historical release, pass
its full lowercase 40-character SHA with `--revision`.

Do not use `git pull` on either production machine as the deployment step. The
CLI invokes each host's guarded release script with the selected SHA. Spark's
script also updates `/home/madslundt/HomeCompute` as its source checkout, then
deploys the pinned SHA into an immutable release directory.

## Deployment behavior

`all` stops after the first failed deployment. There is no automatic rollback
of application data or model artifacts, and no automatic cross-host route
smoke test; each host's existing deployment health checks must pass. Run
`doctor` and verify the actual client route after a rollout.

The operator still reviews and merges changes before deployment. GitHub
Actions only validates and proposes dependency updates; it has no SSH access
to the home network.

When an approved maintained image or Homepage configuration blocks a full
release, follow [release override reconciliation](operations/release-override-reconciliation.md).
Capture the running settings in source first, then use its hash-bound archival
lifecycle. Never discard an override marker to bypass a deployment guard.

## Deploy one host

```bash
./scripts/homecompute deploy home-core
./scripts/homecompute deploy home-spark
```

To deploy a historical revision, provide the complete 40-character SHA:

```bash
./scripts/homecompute deploy home-core --revision FULL_SHA
./scripts/homecompute deploy home-spark --revision FULL_SHA
```

The tool sends the existing core release script to `home-core`. That script
keeps its immutable release checkout, lock, build-before-switch, Compose
validation, health checks, and deployed revision tracking.
If NixOS reports only `homecompute-nas-recovery-backup.service` as the deferred
exit-75 active-Hermes-session retry, the wrapper continues only after confirming that the
active system generation matches the built release, the unit has no running
process, and systemd reports no failed units. The backup retry remains enabled.
Other NixOS switch failures still stop the deployment.

On Spark, `deploy-home-spark.sh` keeps small Git checkouts in
`/srv/homecompute/releases/<sha>`, with `/srv/homecompute/current` and
`previous` pointers and an atomic revision file. This location is suitable for
the code checkout because Spark already uses `/srv/gb10-ai` for HomeCompute
application storage; model artifacts stay there and are not copied into
releases. Before creating a release, the script requires a clean `master`
checkout at `/home/madslundt/HomeCompute`, fast-forwards that checkout from
GitHub, and makes the selected exact-SHA release from it. A dirty or diverged
source checkout is preserved and stops deployment. The script migrates older
trusted compute configuration schemas before validation, preserving configured
values. It validates the release against the host's configured compute tuple.
It does not call `apt upgrade` or update DGX OS, kernel, drivers, CUDA, firmware,
Docker, or NVIDIA Container Toolkit; those remain vendor-managed.

If `text-primary` is already running, the wrapper runs its install, readiness,
and model smoke path. The current roster intentionally keeps that baseline
stopped, so a normal Spark release will not start it as a side effect. In that
case the wrapper records the repository release after validation; it does not
claim the active automation-MoE, Gemma, speech, or adapter Compose projects have
been reconciled. Those remain on their dedicated guarded lifecycles and
qualification gates.
This preserves cold-swap behavior but means the Spark revision pointer tracks
the application checkout, not a single atomic revision for every independently
managed runtime project. Inspect `homecompute services` after deployment.

`diff` compares a host's deployed SHA with a selected revision using local Git
objects:

```bash
./scripts/homecompute diff home-core --revision FULL_SHA
./scripts/homecompute diff home-spark --revision FULL_SHA
```

## Rollback

Use the previous successful revision shown by status/revision files and run the
same guarded deployment command with that SHA:

```bash
./scripts/homecompute deploy home-core --revision PREVIOUS_FULL_SHA
./scripts/homecompute deploy home-spark --revision PREVIOUS_FULL_SHA
```

This revalidates and redeploys the historical code/configuration; it does not
restore database snapshots, model artifacts, or data migrations. Keep the
existing application-specific backup and migration procedures in force. For
home-core, the release script still retains `/srv/homecompute/previous` and
`/var/lib/homecompute/deployed-revision`. For Spark, it retains the previous
release pointer and exact SHA. Neither host garbage-collects old releases.

## Migrating from manual host-side deployment

1. Keep the checkout and SSH host aliases on the operator workstation.
2. Add and verify both host keys in `known_hosts`; do not disable host-key
   checking.
3. Confirm each account has sudo permission for deployment and Docker access
   for a complete service inventory. `home-core` already disables the wheel
   password requirement in its NixOS configuration. Spark can use an
   interactive terminal sudo prompt; configure its sudo policy through the
   supported host administration procedure if unattended deployments are
   needed.
4. Publish reviewed changes and wait for repository CI, then run
   `./scripts/homecompute status` and `./scripts/homecompute deploy all`.
5. Stop using `git pull` as a production deployment step. Existing host
   checkouts can remain for recovery until verified deployments use the new
   release paths.

`home-spark` needs one-time root-owned `/srv/homecompute` and
`/var/lib/homecompute` paths, created by its first deployment. Without Docker
access, status marks container inventory unknown; it does not block a
deployment. The CLI does not modify sshd, sudoers, Docker group membership, or
host-key policy.

## Current limitations

- Services output lists observed Docker containers when the SSH account has
  access; otherwise the inventory is unknown. It does not yet parse each
  host Compose file into a complete expected-versus-actual service matrix.
- `doctor` checks executable presence and Docker access. It does not yet prove
  package declaration for each tool; NixOS remains authoritative on home-core,
  while the guarded setup scripts validate Spark's vendor/runtime prerequisites
  at deployment time.
  Password-protected sudo is reported as an interactive deployment requirement,
  not a host health failure; deployment still requires sudo authentication.
- Model actual running/installed state stays `unknown` until a safe live model
  inventory can be joined to catalog IDs. The existing guarded setup scripts
  remain authoritative for artifacts and model lifecycle.
- Model update findings reuse the existing monitor report. GitHub Dependabot,
  flake PR state, and CI failures are not fetched by the CLI.
- The Spark release wrapper deploys the guarded text baseline only. Speech
  deployments, opt-in model swaps, and their rollback continue through their
  dedicated commands.
- `deploy all` has no authenticated cross-host API smoke test because secrets
  are deliberately not loaded into the workstation CLI.
- A successful code rollback cannot reverse database migrations, and a failed
  Spark installer can leave runtime changes even though release pointers stay
  on the last recorded success.

## Platform readiness and recovery

`homecompute-platform-health.service` now checks the deployed consumer paths.
Its read-only helper enters the existing LiteLLM container to check the actual
configured text, embedding, transcription and speech upstreams. This preserves
the host firewall's denial of direct SSH-forward access. Core Wyoming Describe
checks use the two existing private adapter addresses. Probes send no inference
requests and emit only finite readiness booleans. Required dependency failure
writes a zero gauge and fails the unit; unit success is no longer independent
of model readiness. A stopped optional vision profile is not a required check.

The approved service inventory is `config/system-monitoring.json`; the trusted
snapshot collector must include the same entries when installing its bundle.
Keep deliberately retired services out of the required inventory. The expanded
core inventory includes Immich, Mattermost, embeddings, speech adapters and CPU
fallbacks, TTLock, the compute tunnel, Codex VM, and local backup status. The
fixed inventory also covers the active Omi, book, Chessmate, homepage, model
manager and proxy containers. Deliberately stopped image studio and historical
rollback containers remain outside the required inventory.

When NAS recovery is installed, readiness also requires authorized, active
backup/check schedules, successful service state, a completed encrypted snapshot
within 36 hours, and a passed repository check within eight days. Missing,
unsafe, malformed or future-dated evidence fails closed. These checks read only
bounded private metadata and publish booleans; they never expose snapshot IDs,
credentials or application content. A backup deferred because writers are busy
is retried automatically after 30 minutes. Freshness remains unready until a
completed encrypted snapshot exists within the required window.
The home-core release wrapper can continue after a NixOS switch reports a
failure only for the exact active NAS-backup deferral: the selected generation
is active, the backup timer remains active, and the backup service is in its
exit-75 automatic retry state with no process. If platform health also failed,
the wrapper additionally requires a fresh, root-owned metrics file showing
NAS backup freshness as the only failed readiness signal. The health unit stays
failed and readiness stays false until a fresh archive exists; the wrapper does
not clear the failed state, cancel the retry, or claim the platform is healthy.
Any other failed unit or readiness signal still stops deployment.
Readiness accepts a scheduled targeted installation or its retired receipt after
canonical NixOS reconciliation. Retirement still requires authorization, active
schedules, successful services and fresh backup/check receipts.

For an authorized repair that must preserve unrelated live application state,
`scripts/setup-platform-observability.py` installs readiness, the finite machine
collector, their approved registry and the bounded conversation evidence
projection from an exact published release. The reader update permits only its
new inventory projection, the matching conversation call and explanatory hint;
every other AST node must match the installed module. Unknown reader drift
aborts before service or registry changes. Its existing owner/mode and exact
before/after hashes are retained with the registry backup. The previously active
adapter is stopped while both files change and resumed after validation;
rollback restores both before reloading it. An inactive adapter stays inactive,
and later unrelated reader changes block rollback instead of being overwritten.
The installer does not reinstall the communication bundle or change household
tools, credentials, providers, channels, containers or inference settings. Its default prints the
reviewable bundle hash without writing anything:

```bash
sudo python3 /srv/homecompute/releases/FULL_SHA/scripts/setup-platform-observability.py
sudo python3 /srv/homecompute/releases/FULL_SHA/scripts/setup-platform-observability.py \
  --revision FULL_SHA --expected-hash REVIEWED_BUNDLE_HASH --apply
sudo python3 /srv/homecompute/releases/FULL_SHA/scripts/setup-platform-observability.py --status
```

The release checkout must match its committed files and be contained in
`origin/master`; a dirty checkout or unpushed commit is refused. Installation
retains an immutable version under `/opt/homecompute/observability/releases`,
backs up the exact previous registry and unit drop-ins, and pauses only the two
metadata collectors while applying. Existing Home Assistant admission is
preserved. The native adapter is restarted to reload the registry, while its
existing code and private state stay in place.

On NixOS, `/etc/systemd/system` resolves into the immutable store. This lifecycle
therefore uses reviewed `90-homecompute-observability.conf` drop-ins below
`/etc/systemd/system.attached/<unit>.service.d/`. Drop-ins are collected even
when a higher-priority Nix base unit is selected; installation verifies the
effective paths, UNIX socket access and timeouts before running fresh probes.
A real degraded dependency is reported, rather than rolling back truthful
monitoring. Installation failures restore the previous files automatically.

The receipt records the exact revision, bundle hash and private backup ID.
An explicit rollback restores that saved pre-installation state:

```bash
sudo python3 /srv/homecompute/releases/FULL_SHA/scripts/setup-platform-observability.py \
  --expected-hash INSTALLED_BUNDLE_HASH --rollback
```

After the normal NixOS and communication lifecycles reconcile the same probe,
collector, workflow-health dependency and inventory, remove the managed
overlays with `--retire --expected-hash INSTALLED_BUNDLE_HASH`. Retirement
refuses while the underlying units still reference older source. This accounts
for the temporary deployment overlay instead of leaving an unexplained host
override.

The first off-host destination is the dedicated Synology SMB3.1.1 share
`//192.168.30.236/homecompute-backups`; Hetzner is deferred. The reproducible
external NAS policy is `config/synology-backup.json`. In DSM, create the Btrfs
share on Volume 1 with data checksums, both hide options enabled and no recycle
bin. Create `homecompute-backup` in the normal `users` group, with SMB as its
only allowed application. Explicitly deny DSM, AFP, FTP, File Station, SFTP,
Universal Search and rsync, and deny the existing `homeassistant`,
`immich-library` and `omi` shares. On the new share, grant read/write to the
backup account and administrator, and explicitly deny `guest`, `homeassistant`
and `immich-svc`. Read back these settings before admitting a credential.

Provision the SMB password only through
`scripts/provision-nas-backup-credentials.py provision` in the operator's
private terminal. Its root-owned mode-0600 file is
`/etc/homecompute/nas-recovery-backup.credentials`; the password remains in the
owner's password manager and never enters source, commands or logs. Restic uses
the existing SOPS-managed `/run/secrets/restic/password`, creating no additional
local-only encryption secret. Neither the Immich account/media share nor Omi's
NFS export is reused. An absent or different mount never falls back to local
storage.
`findmnt` may report the ordered `systemd-1` autofs underlay followed by CIFS
at the same target: only that exact stack or exact CIFS alone qualifies as the
backup destination. Exact autofs alone is accepted only while the installer
stages the mount, never while validating a backup destination.

The NAS backup now captures only the Immich library, an application-consistent
Immich PostgreSQL dump, and Hermes native-home agent state from
`hermes-personal-v2`. The Hermes exporter uses the SQLite online backup API and
refuses active sessions. It excludes `.env` files, generated `config.yaml`
files, logs, caches, and links. Rebuildable containers/images, other application
state, VM disks, and guest operating-system files are outside this backup.

The root-only Restic repository is `/mnt/homecompute-backups/restic-homecompute-selected`.
The source is read-only during transfer. A dedicated forced-command SSH key on
home-core can invoke only the Hermes exporter from agents VM address
`10.77.20.1`; its private key is SOPS-managed and the guest host key is pinned
in source. Initialize and verify the repository, then create and inspect a real
snapshot:

```bash
sudo systemctl start homecompute-nas-recovery-initialize.service
sudo systemctl start homecompute-nas-recovery-proof.service
sudo systemctl start homecompute-nas-recovery-backup.service
sudo systemctl start homecompute-nas-recovery-check.service
```

For a selected-snapshot restore drill, first check free space for the media
library, then use a new direct child of the scratch root:

```bash
sudo homecompute-nas-recovery restore-selected \
  --snapshot FULL_64_CHARACTER_SNAPSHOT_ID \
  --target /srv/restore-tests/selected-YYYYMMDD
```

The daily backup retains 30 daily, 12 weekly, and 24 monthly snapshots. The
weekly repository check reads a bounded data subset. The Restic snapshot records
only `/mnt/immich-nas`, `/srv/state/immich/db-backups`, and the temporary Hermes
native-home archive. The transient Hermes archive is removed after a completed
snapshot; a failed run leaves its root-only file for diagnosis and retry.

Restore drills use `nas-recovery-backup.py restore-selected` with an exact full
snapshot ID and a new direct child under `/srv/restore-tests`. It verifies and
extracts Hermes native-home state and SQLite databases into that scratch path,
checks the Immich media tree, and reads complete compressed SQL dumps. A real Immich database import and service boot
must be verified separately before claiming application recovery. The backup
share and Immich media share are on the same Synology volume, so this protects
against home-core loss but not Synology or site loss. Keep the Restic password
and host age identity in separate offline custody.
