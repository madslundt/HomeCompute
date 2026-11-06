# Assistant-context-only NAS restore qualification

[`scripts/restore-assistant-context-nas.py`](../../scripts/restore-assistant-context-nas.py)
is a root-only, home-core operation for qualifying assistant state from one exact
Restic snapshot. It reads the repository through Restic's foreground read-only
FUSE mount and uses the pinned full-ID path template `ids/%I`; it never selects
`latest` or a short snapshot prefix. The exact archive is opened as a seekable
tar stream so unrelated database, VM, and seed payload bodies are skipped.

The helper stages only the assistant paths declared by `manifest.json`, applies
the existing assistant path allowlist, rejects links/traversal/special files,
checks private data permissions (no executable or special bits) and SQLite integrity, and writes a pending restore
fence before extracting data. The archive's own fence is ignored. It writes a
separate `last-assistant-context-nas-verification.json` receipt with source,
snapshot, archive, manifest hash, per-member hashes, and counts. A successful
receipt means **assistant-context-only verification**. It does not verify or
restore PostgreSQL, VMs, the VM seed, the full application archive, or perform
deletion-ledger admission. The extracted state remains fenced pending until the
separate trusted replay/admission lifecycle completes.

From the selected HomeCompute checkout or immutable release, first print the
read-only source plan. It reads only the four listed source files and does not
require root, Git commands, NAS access, or a mount:

```sh
python3 scripts/restore-assistant-context-nas.py plan
```

For a live restore, run the helper from the exact published release after
checking its NAS backup and deployment runbooks. Pass the `source_bundle_sha256`
from the plan along with the installed NAS bundle hash, full Restic snapshot ID,
exact archive filename, and a new target name:

```sh
sudo /run/current-system/sw/bin/python3 \
  /srv/homecompute/releases/FULL_RELEASE_SHA/scripts/restore-assistant-context-nas.py restore \
  --revision FULL_RELEASE_SHA --expected-source-hash BUNDLE_SHA256 \
  --installed-hash INSTALLED_NAS_BUNDLE_SHA256 \
  --snapshot FULL_RESTIC_SNAPSHOT_ID \
  --archive homecompute-applications-YYYYMMDDTHHMMSS.ffffffZ.tar \
  --target-name assistant-context-qualification
```

The command validates the current backup receipt and snapshot metadata against
the dedicated NAS scope before mounting. It uses an owned private mountpoint
under `/srv/restore-tests`, does not use `--allow-other`, and creates the
success receipt only after unmount and child-process settlement. Failed
extraction remains behind its pending fence. If FUSE cleanup cannot be proven,
the mountpoint and staged files are preserved for operator recovery; do not
remove a path until independently confirmed unmounted.

Focused synthetic coverage:

```sh
python3 tests/assistant-context-nas-restore-test.py
```

This test uses a generated seekable tar and does not access NAS or live restore
paths.
