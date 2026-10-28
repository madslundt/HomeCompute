# Isolated application-engine restore verification

The accompanying backup append also retains Chessmate's external mode-0600
`config/.env` as the private encrypted `state/chessmate/runtime.env`; only that
file and its certificate directory are exposed read-only through the backup
unit's otherwise hidden home directories. Chessmate code/Compose remain owned
by the separate `madslundt/ChessMate` Git repository. Treat the archived runtime
environment as secret durable data, never source or terminal output.

The NAS `restore-verify` command checks restored SQLite files, dump checksums,
seed material and qcow2 structure. It deliberately reports PostgreSQL imports
and VM boots as unverified. `scripts/verify-application-recovery.py` supplies
these separate engine checks against the actual restored archive. It does not
restore production or claim authenticated application functionality.

Run it only from a reviewed release published on `origin/master`, after the
targeted NAS lifecycle has completed the first backup and `restore-verify` for
the same full snapshot ID and exact archive filename. The input is the
root-only `-application` directory that command created directly under
`/srv/restore-tests`. Keep the original restored archive and tree intact.

```bash
sudo python3 -I /srv/homecompute/releases/FULL_SHA/scripts/verify-application-recovery.py \
  --restore-directory /srv/restore-tests/nas-archive-YYYYMMDD-application \
  --snapshot FULL_64_CHARACTER_SNAPSHOT_ID \
  --archive EXACT_APPLICATION_ARCHIVE_NAME
```

The helper verifies its committed source and policy bytes, requires a matching
prior application-verification receipt binding the exact resolved target and
manifest SHA-256, validates the restored manifest and archived agents seed,
and refuses symlinked engine inputs or external restored links. A receipt
created before target binding was added must be replaced by another isolated
`restore-verify` into a fresh directory using the updated lifecycle. It serializes
engine drills with a dedicated scratch lock. It creates a new private
`/srv/restore-tests/engines-*` workspace; it never overwrites the input tree,
production state, credentials, units, containers or backup repositories.

Four PostgreSQL scopes run sequentially: gateway, Mattermost, Immich and
Chessmate. The policy pins the exact already-installed local image IDs, major
versions and extension versions. Missing images fail; no image is pulled.
Temporary containers have no network, published ports or host storage mounts,
run as the image's non-root PostgreSQL UID, drop all capabilities, and use a
read-only root filesystem plus bounded private tmpfs. Each has one CPU, 1 GiB
memory and a process limit. A unique bootstrap superuser/database lets the
`pg_dumpall --clean` stream recreate the original roles and databases. The
import uses `ON_ERROR_STOP`, then checks nonempty table counts and exact
extension metadata. SQL and command output remain private. Ownership is checked
using a unique container name, random label and exact container ID before
cleanup; uncertain cleanup prevents further engine launches.

Both VMs run sequentially using the existing protected QEMU package GC root.
Their q35/KVM, virtio disk ordering, Codex work-disk serial and archived seed
layouts follow their owning sources, with test resources reduced to one vCPU
and 2 GiB. Disk inputs are inherited read-only file descriptors; QEMU writes
only disposable snapshot overlays in its private working directory. Backing
files are refused. The QEMU process runs as `nobody` with only the KVM
supplementary group, in a new network namespace with no virtual NIC or host
ports. Its CPU time, address space, serial-log size and wall time are bounded.
The helper terminates only the exact child process it started.

A VM passes only when its fresh private serial log contains a Linux kernel,
systemd userspace and the expected hostname's serial login prompt. A running
QEMU process or QMP status cannot pass. The archived agents seed is mandatory;
there is no external fallback. A boot timeout is a failed gate, including when
the resource reduction or unavailable prerequisite prevents boot.

The JSON result and private `receipt.json` bind booleans/counts to the source
revision, NAS snapshot, archive name and manifest hash. Exit zero requires all
four PostgreSQL imports and both VM boots to pass. Error output contains fixed
categories; private SQL and guest logs must not be pasted into chat or Git.
INT, TERM and HUP are deferred while a child handle is recorded and while
bounded owned cleanup runs; the child unmasks those signals before execution.
An interrupted drill attempts owned cleanup and exits nonzero. Keep the
private workspace for diagnosis; deleting it later is a separate explicit
operator action after confirming no owned engine remains.

Even a passing engine receipt leaves `application_services_verified=false`.
It proves restored database engines and guest boot, not authenticated agent
work, external integration behavior, independent NAS media protection, offline
secret custody or recovery from loss of both host and NAS. No notification or
cron traffic can leave the test guests because their networks are absent.

Focused tests run without production services:

```bash
python3 tests/application-recovery-engine-test.py
HOMECOMPUTE_SYNTHETIC_POSTGRES=1 python3 tests/application-recovery-engine-test.py
```

The optional second command starts only new local PostgreSQL clusters under a
temporary private directory, with Unix sockets and TCP disabled, to test clean
role/database recreation. It never connects to an existing PostgreSQL server.
This synthetic result is separate from the real NAS engine receipt.
