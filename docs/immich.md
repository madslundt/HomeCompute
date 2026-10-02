# Immich, off-site backup, and Google Photos migration

## Current implementation state

The repository contains a pinned, LAN/Tailscale-only Immich Compose project,
NixOS state and environment configuration, the reusable Restic source and
destination model, and operator tooling for database dumps, Google Takeout
imports, and isolated Restic restores. Immich is enabled in the host
configuration and its database password is provisioned in SOPS. The Takeout
import and Hetzner backup remain gated until their account-specific credentials
are ready. No service has been deployed, no remote repository has been
initialized, and no restore drill or Google Takeout import has been performed.
Those are release gates, not completed acceptance criteria.

Immich v3.2.4 is pinned by its Linux x86_64 image digests in
[`config/immich.env.example`](../config/immich.env.example). The database image
is the PostgreSQL 14 VectorChord image from Immich's official v3.2.4 Compose
file. The app release is listed at
[Immich v3.2.4](https://github.com/immich-app/immich/releases/tag/v3.2.4).

## Architecture and storage

The Immich deployment lives at `deploy/immich/compose.yaml`. NixOS owns host
state, directory setup, generated non-secret environment, and SOPS secret
declarations. Compose owns the application services. Hetzner credentials are
available only to Restic; Immich has no cloud-backup credentials.

| Host path | Use | Backup |
| --- | --- | --- |
| `/srv/state/immich/database` | PostgreSQL 14 / VectorChord data on local NVMe | No live-file backup; use SQL dump |
| `/srv/state/immich/library` | Originals and Immich-managed media | Yes |
| `/srv/state/immich/model-cache` | ML models | No, rebuildable |
| `/srv/state/immich/redis` | Valkey state | No, rebuildable |
| `/srv/state/immich/db-backups` | Atomic, compressed `pg_dumpall` output | Yes |
| `/srv/state/immich/import` | Temporary Takeout staging | No |

`homecompute.immich.libraryPath` controls the host path mounted at `/data` in
Immich. PostgreSQL remains under `stateRoot/database` on local SSD. To migrate
to a NAS later, stop uploads and Immich, copy the library with `rsync`, compare
checksums, mount the NAS, change `libraryPath`, start Immich, and verify sample
assets. Keep the former library copy until the NAS-backed service and next
Hetzner backup have been checked. Do not put PostgreSQL on NFS or SMB.

### Synology SMB mount

`home-core` mounts the Synology share `//192.168.30.236/immich-library` at
`/mnt/immich-nas` using CIFS/SMB 3.1.1. The mount is systemd-automounted and
does not block boot when the NAS is offline. Its credentials are supplied by
the root-only SOPS secret `/run/secrets/immich/synology-smb-credentials`, in
standard `mount.cifs` format:

```text
username=immich-svc
password=<entered locally; do not commit or share>
```

Before deploying the mount, run
`scripts/set-synology-immich-secret.sh` and paste the password at its hidden
prompt. It asks twice, sends the value to SOPS through stdin, and updates only
`secrets/synology-immich.sops.yaml`; that dedicated file contains no other
credentials. The password is not placed in shell history, process arguments,
or the Nix store. After deployment, access
`/mnt/immich-nas` to trigger the mount and verify it with `findmnt` and a small
test file. The active Immich library remains on local storage until a separate
copy, checksum comparison, and cutover are completed. The Immich database
stays on the local SSD.

The web/API service listens at `http://192.168.30.122:2283` on the LAN and
`http://100.110.248.102:2283` on Tailscale. Loopback is also bound for local
maintenance. No public reverse proxy or router forwarding is configured.
Only `immich-server` publishes a port; its database, Valkey, and ML services
have no host ports.

Images are immutable SHA256 references for Linux x86_64. The starting resource
limits are 4 CPU / 4 GiB for Immich server, 4 CPU / 4 GiB for ML, 2 CPU / 2 GiB
for PostgreSQL, and 0.5 CPU / 512 MiB for Valkey. Review usage before changing
them. Immich requires its database on a local filesystem; the upstream
[requirements](https://docs.immich.app/install/requirements/) specifically
recommend local SSD storage.

## Provisioning secrets and enabling services

The SOPS keys used by the NixOS module are:

```text
immich/database-password
immich/import-api-key
restic/hetzner/password
restic/hetzner/ssh-key
```

The encrypted document contains `immich/database-password`, generated as a
random alphanumeric value and verified with home-core's existing age identity.
The remaining entries are `immich/import-api-key`,
`restic/hetzner/password`, and `restic/hetzner/ssh-key`. In a writable trusted
Git checkout on a machine with the configured age identity, add the import key
after creating the Immich administrator and add the Restic credentials after
the Storage Box account and dedicated SSH key are ready:

```sh
sudo env SOPS_AGE_KEY_FILE=/var/lib/sops-nix/key.txt \
  sops secrets/home-core.sops.yaml
```

Use a randomly generated alphanumeric Immich database password, as required
by Immich's Compose environment contract. Keep the `import-api-key` scoped to
the import user. Generate a dedicated SSH key for this backup destination;
do not reuse a personal administration key. The key should be accepted only
by the Storage Box account. Never copy decrypted values into Git or a Nix
expression.

After the import API key has been added, set
`homecompute.immich.importEnabled = true` in `hosts/home-core/default.nix`.
Immich itself can deploy independently of the Takeout import tooling. Deploy
NixOS, and then deploy the same reviewed commit with:

```sh
sudo bash scripts/deploy-home-core.sh FULL_COMMIT_SHA
```

The deploy script validates Compose, pulls the four digest-pinned images, and
waits for service health. Create the Immich administrator manually through
the LAN or Tailscale URL. Upload a few test images and a video, run an ML job,
and confirm the library survives a container and host restart before importing
the historical library.

## Hetzner Storage Box and Restic

Purchase a 1 TB BX11 in an EU location. Use the account-specific hostname and
username in `homecompute.backups.destinations.hetzner.repositoryBase`; the
sample `uXXXXX` value deliberately fails evaluation while backups are enabled.
The repository base should be shaped like:

```text
sftp:u123456@u123456.your-storagebox.de:/backups/homecompute
```

The Immich source gets its own repository under `.../immich`. Later sources
can use independent retention and repositories without changing Immich or the
destination implementation.

Create `/etc/homecompute/restic/hetzner_known_hosts` as a root-owned `0600`
file. Retrieve the Storage Box host key from the official Hetzner source, then
compare its fingerprint with an independently obtained Hetzner fingerprint.
`ssh-keyscan` may collect the key but is not proof of identity by itself. The
Restic SSH command uses `StrictHostKeyChecking=yes`, `IdentitiesOnly=yes`, the
dedicated SOPS SSH key, and this pinned known-hosts file. Missing or changed
host keys fail the unattended backup.

The encrypted Restic password is separate from the Immich database password.
After the host key, identity, account-specific repository base, and SOPS
entries are present, set `homecompute.backups.enable = true`, deploy NixOS,
and initialize the repository explicitly before relying on its timer:

```sh
sudo restic-homecompute-immich-hetzner init
sudo systemctl start restic-backups-homecompute-immich-hetzner.service
sudo systemctl start restic-backups-homecompute-immich-hetzner-check.service
```

The NixOS Restic jobs keep `initialize = false`; a missing repository never
silently becomes a new empty production repository. The source runs daily
with a 30-minute randomized delay and retains 30 daily, 12 weekly, 24 monthly,
and 10 yearly snapshots. A separate weekly job runs `restic check` without a
full data read. Database preparation runs first and a failed dump fails the
backup job. The helper writes a compressed dump to a same-directory temporary
file, fsyncs it, atomically renames it, and keeps the latest 14 local dumps.

Machine-readable status is written below
`/var/lib/homecompute/backup-status/`:

```text
immich-last-backup
immich-last-backup-failure
immich-last-check
immich-last-check-failure
```

Files contain UTC time, systemd result, and (for a successful backup) the
Restic raw repository size in bytes when available. Backup age is computed
from the timestamp. The systemd journal retains the detailed command error.

## Google Takeout import

Google Photos is a one-time source. Use [Google Takeout](https://takeout.google.com/),
select only Google Photos, export the complete library, and choose manageable
10–50 GB archive parts. Download every part. Do not reorganize extracted files
or delete the JSON metadata sidecars. Stage the archives as-is under:

```text
/srv/state/immich/import/google-takeout/
```

Run the pinned `immich-go` v0.32.0 import helper from home-core (or a trusted
operator machine with the same Immich URL and API-key secret available):

```sh
sudo IMMICH_URL=http://192.168.30.122:2283 \
  IMMICH_API_KEY_FILE=/run/secrets/immich/import-api-key \
  scripts/import-google-photos.sh \
  /srv/state/immich/import/google-takeout
```

The helper downloads the Linux x86_64 v0.32.0 release into a temporary
directory and checks SHA256
`6e2ad86bafdadb9466d6515de7cb882726c0aea1a21d51164dff361d7d480a97` before
execution. It supplies the API key and server URL through immich-go's
`IMMICH_GO_UPLOAD_API_KEY` and `IMMICH_GO_UPLOAD_SERVER` environment
configuration, never a command-line argument, and never prints the key. Its upstream release is
[immich-go v0.32.0](https://github.com/simulot/immich-go/releases/tag/v0.32.0),
the version supporting Immich v3. Do not run this helper during normal service
deployment. It refuses an empty Takeout directory and leaves all source ZIPs
in place.

After import, compare Immich's asset count against Takeout's inventory (the
expected order of magnitude is about 30,000 items). Check samples from early,
middle, and recent years; videos; albums; edited images; and Live Photos or
motion photos where present. Confirm capture dates, EXIF, GPS data, album
membership where supported, and video playback. Save the import output and a
short count/sample report. Do not delete the Takeout archives or Google Photos.

Install the Immich iPhone app only after the historical import is checked.
Enable automatic upload and confirm a new phone photo arrives and is included
in the first successful Restic snapshot. Google Photos may remain an independent
copy during the transition.

## Restore procedure and release gate

Restore drills must use an empty temporary destination, never production
paths. List snapshots with the generated wrapper and restore one with:

```sh
sudo restic-homecompute-immich-hetzner snapshots
sudo scripts/restore-immich.sh SNAPSHOT_ID /srv/restore-tests/immich-YYYYMMDD
```

The helper restores only `library` and `db-backups` into that directory and
refuses an existing target or a path under `/srv/state/immich`. Verify:

1. The restored SQL dump passes `gzip -t` and can be loaded into a temporary
   PostgreSQL 14 / VectorChord container using the pinned database image.
2. Sample photo and video files exist, have non-zero sizes, and match expected
   checksums from the source library where a source copy is available.
3. Immich can start against a scratch copy of the restored database and
   restored library, with no host port publication and no production volume
   mounted.
4. The scratch instance shows expected asset counts, metadata, albums, and
   sample photo/video playback.

Record every drill in `docs/immich-restore-log.md` using the template below.
Google Photos is optional only after the historical import, automatic phone
upload, first Hetzner backup, weekly integrity check, and a successful complete
restore drill have all passed. The implementation currently has no such
restore evidence, so Google Photos must remain intact.

```text
Date:
Restic snapshot ID:
Restore destination:
Files restored:
Database dump restored into scratch PostgreSQL:
Photos and videos checked:
Metadata/albums checked:
Scratch Immich startup result:
Result and operator:
```

## Operations and troubleshooting

- Inspect service state with `docker compose --env-file
  /etc/homecompute/immich.env -f deploy/immich/compose.yaml ps` from the
  release checkout. Use `config --quiet`; printing rendered Compose config
  exposes the database environment to the terminal.
- Read container logs with `docker compose ... logs --tail=100 immich-server`
  or the relevant service. Never add `set -x` around commands that load SOPS
  values.
- A database permission/startup error: confirm `/srv/state/immich/database`
  is local ext4 and owned by UID/GID 999 with mode 0700. Do not relax it to
  world-writable permissions.
- A backup failure: inspect the matching systemd journal, confirm the
  database dump completed, then check the Storage Box hostname, pinned host
  key, SOPS Restic password, SSH key, and repository path. Do not initialize
  a repository as a recovery shortcut.
- A changed SSH fingerprint is a hard stop until verified with Hetzner.
- A missing library mount or unexpectedly empty library must cause the backup
  to fail. Verify the active `libraryPath` and mount before restarting uploads.

## Architecture extensions

Add another application through `homecompute.backups.sources.<name>` with its
own paths, preparation command, schedule, and retention, then name one or more
configured destinations. This is the intended extension point for a later
Home Assistant native backup staged on home-core, n8n, or another Restic
destination. Do not put Hetzner credentials in the application project.

The source/destination split also permits a second cloud destination later.
No second provider or Home Assistant backup is configured by this change.
