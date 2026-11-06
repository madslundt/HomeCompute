# Empty synthetic-only assistant recovery admission

This procedure qualifies an isolated restore only when both the selected
archive and the fixed live ContextStore contain no assistant source events,
current projections, pending events, invalidations or memory records for either
enabled scope. It does not admit household data, establish provider deletion
completeness, or enable the production service. If any of those tables contain
rows, stop and use separately reviewed current source-controller evidence; do
not use this synthetic-only command.

## Fresh cold archive and isolated extraction

Use the published operator helper from the selected release and the installed
NAS bundle hash from its private receipt. The plan command is read-only and
prints the exact operator bundle hash to review:

```sh
revision=FULL_PUBLISHED_SHA
nas_hash=INSTALLED_NAS_BUNDLE_HASH
recovery_tool=/srv/homecompute/releases/$revision/scripts/backup-application-recovery.py
sudo python3 "$recovery_tool" plan
```

After coordinating the maintenance window, the source-owned operator command
holds only its bounded inference admission drain, waits for existing requests,
and invokes the existing guarded cold-backup service. Existing work is not
killed; a drain timeout starts no backup. Supply the plan hash reviewed above
and the exact installed NAS hash:

```sh
sudo python3 "$recovery_tool" backup --revision "$revision" \
  --expected-hash REVIEWED_OPERATOR_BUNDLE_HASH \
  --installed-hash "$nas_hash" --acknowledge-pause --drain-timeout 120
```

Require a new successful `last-backup.json` receipt, a settled successful
backup unit, completed source resumption, and no retained recovery journal.
Select the matching Restic snapshot and exact application archive from that
receipt. Do not treat a deferred exit 75 or an older archive-verification
receipt as fresh evidence.

Use a new direct child of `/srv/restore-tests` for the NAS extraction. Pass the
selected published revision so the application archive is checked with the
current verifier rather than the retired installed verifier. Then run the
source-owned NAS `verify-extracted` operation against the exact retained tar
and existing extraction; this refreshes the root-only verification receipt
without downloading or extracting a third full copy. The assistant staging
operation adds only a pending fence to that same isolated tree:

```sh
nas_tool=/srv/homecompute/releases/$revision/scripts/setup-nas-recovery-backup.py
restore_tool=/srv/homecompute/releases/$revision/scripts/assistant-context-restore.py
nas_target=/srv/restore-tests/nas-archive-YYYYMMDD
application_target="$nas_target-application"
archive=EXACT_APPLICATION_ARCHIVE_NAME
snapshot=FULL_64_CHARACTER_SNAPSHOT_ID

sudo python3 "$nas_tool" restore-verify --expected-hash "$nas_hash" \
  --revision "$revision" --snapshot "$snapshot" --archive "$archive" --target "$nas_target"
restored_tar="$nas_target/srv/backups/agent-recovery/$archive"
sudo python3 "$nas_tool" verify-extracted --expected-hash "$nas_hash" \
  --revision "$revision" --snapshot "$snapshot" --archive "$archive" \
  --restored-archive "$restored_tar" --target "$application_target"
sudo python3 "$restore_tool" stage-verified-extracted --revision "$revision" \
  --snapshot "$snapshot" --archive "$archive" --target "$application_target"
```

The NAS verifier checks the complete application archive, including its
manifest, SQLite integrity, compressed PostgreSQL dumps and VM disks. The
assistant stage command requires its latest root-only receipt to match the
current snapshot, exact archive name, selected revision, extraction target and
manifest hash. It checks the assistant database inventory/modes and writes a
private pending fence into the already verified scratch tree; it performs no
additional archive download or extraction. PostgreSQL imports and VM boots
remain unverified and are not prerequisites for this isolated assistant DB
gate. No production state is installed or replaced.

## Point-in-time synthetic admission

Export a content-free ledger from the fixed live database at
`/srv/state/assistant-context/context.sqlite3`. The root-only command reads live
database/WAL bytes into private, consistency-checked snapshots and queries only
those copies, avoiding root-owned SQLite sidecars in live state. It includes
both enabled scopes. Its result is explicitly
`freshness=unverified` and is not admission by itself:

```sh
ledger_name=assistant-context-ledger-UNIQUE_ID.json
sudo python3 "$restore_tool" export-live-ledger --output-name "$ledger_name"
ledger="/run/homecompute/$ledger_name"
```

Review the exact output and hash. The operator then supplies that hash to the
narrow `admit-empty-synthetic` command. It requires the target's pending fence,
re-exports both scopes from a transaction on a fresh consistent copy of the
fixed live ContextStore, and requires byte-for-byte ledger equality. It also checks that
both the live and archived ContextStore have zero source events, current rows,
pending rows, invalidations, memory rows/requests and memory tombstones, and
that all represented live and archive scopes are included. It writes a new
root-only proof under `/run/homecompute` with exclusive creation:

```sh
ledger_hash=REVIEWED_LEDGER_SHA256
admission_name=assistant-context-admission-UNIQUE_ID.json
sudo python3 "$restore_tool" admit-empty-synthetic \
  --target "$application_target" --ledger "$ledger" \
  --ledger-sha256 "$ledger_hash" --output-name "$admission_name"
admission="/run/homecompute/$admission_name"
```

The proof is a point-in-time operator/source-controller attestation for this
empty synthetic case only. Keep the returned ledger and admission hashes in
operator custody. No model endpoint can choose the live database path, scope
set, expected ledger hash, output directory or proof hash. The command refuses
existing output paths, non-root ownership, stale/non-pending targets, missing
scopes, changed ledger bytes, or any context/memory rows.

Complete only inside the isolated target, selecting both hashes explicitly:

```sh
sudo python3 "$restore_tool" complete --target "$application_target" \
  --ledger "$ledger" --ledger-sha256 "$ledger_hash" \
  --admission "$admission" --admission-sha256 REVIEWED_ADMISSION_SHA256
```

Completion replays admitted tombstones, scrubs source-derived ledgers, marks
the entire archived retrieval vector cache invalid, and changes the staged
fence to `ready` only after all checks pass. A failure leaves the fence pending.
Keep the completed tree isolated and do not start its service or copy it over
production. The helper does not authorize household enablement or live-state
replacement.

## Limits

An empty ContextStore and matching empty ledger prove no assistant source events
were admitted into that store at the checked snapshot. They do not prove that
Home Assistant, Daythread, Aula, Calendar or any future provider has complete
deletion feeds, nor that missing upstream items are deletions. Keep those
provider coverage and deletion-completeness gaps explicit before any real data
is admitted. Normal household recovery still needs separately trusted,
complete current deletion evidence and its own review.
