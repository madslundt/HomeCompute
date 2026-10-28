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
fallbacks, TTLock, the compute tunnel, Codex VM, and local backup status.

For an authorized repair that must preserve unrelated live application state,
`scripts/setup-platform-observability.py` installs only readiness, the finite
machine collector and their approved registry from an exact published release.
It does not reinstall the communication bundle or change household tools,
credentials, channels, containers or inference settings. Its default prints the
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

The intended first off-host destination is a **separate Synology backup share**.
Hetzner is deferred. Neither the Immich media share/account nor Omi's NFS export
is a backup destination. Before enabling `homecompute.nasRecoveryBackup`, supply
its actual `share` and a dedicated root-only, externally provisioned
`credentialsFile`. Declare those non-secret settings in the host module and keep
the credential in SOPS. An absent destination remains a blocked backup, never a
silent same-host fallback.

The NAS module's `includeApplicationState = true` default prepares an
application-consistent recovery archive before Restic transfer. It stops the
declared writers and VMs, uses PostgreSQL dumps and SQLite's transactional backup
API, archives durable application files and both VM disk sets, and resumes only
previously active resources even if preparation fails. Active private browser
sessions defer the job. Preparation and deployments share a lock; the local VM
backup and recovery preparation also exclude each other. The NAS schedule starts
at 03:15 local host time, outside the local VM snapshot's midnight window.

Preparation requires measured space for source files plus 100 GiB headroom and
retains two completed application archives. Archives are root-only on local
storage and encrypted by Restic on the NAS. Original Immich media stays outside
this archive and requires an independent media backup. The host age identity and
Restic recovery password need separate offline custody. Generated Nix store
symlinks are rebuilt from the release instead of copied into a recovery archive.

After deploying the reviewed configuration, validate and initialize the new
repository once, then run transport and application backup checks:

```bash
sudo python3 /srv/homecompute/current/scripts/nas-recovery-backup.py validate
sudo python3 /srv/homecompute/current/scripts/nas-recovery-backup.py init
sudo python3 /srv/homecompute/current/scripts/nas-recovery-backup.py proof
sudo systemctl start homecompute-nas-recovery-backup.service
sudo systemctl start homecompute-nas-recovery-check.service
```

`proof` verifies only encryption, NAS write/read and a synthetic byte restore.
It does not prove application recovery. For a completed application's exact
snapshot ID and archive name, restore into a new scratch directory and verify
the archive into a second new scratch directory:

```bash
sudo python3 /srv/homecompute/current/scripts/nas-recovery-backup.py restore-application \
  --snapshot FULL_64_CHARACTER_SNAPSHOT_ID --archive EXACT_APPLICATION_ARCHIVE_NAME \
  --target /srv/restore-tests/nas-archive-YYYYMMDD
sudo python3 /srv/homecompute/current/scripts/application-recovery.py verify \
  /srv/restore-tests/nas-archive-YYYYMMDD/srv/backups/agent-recovery/EXACT_APPLICATION_ARCHIVE_NAME \
  /srv/restore-tests/application-verify-YYYYMMDD
```

Verification checks restored SQLite integrity, compressed dump checksums and all
three qcow2 disks without touching production paths. It explicitly leaves
PostgreSQL imports and isolated VM boots unverified; complete those drills with
no production network access before accepting disaster recovery. NAS storage
protects against home-core loss, while NAS/site loss remains uncovered until the
independent second destination and its restore drill are complete.
