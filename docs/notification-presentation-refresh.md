# Refresh direct notification presentation

Use `scripts/setup-notification-presentation.py` for the four direct Mattermost
renderers after reviewing, testing, committing and pushing their source. It is
the owning narrow lifecycle for this presentation update. Do not rerun the broad
communication/access installers for this change: they replace unrelated sources,
configuration and units.

The fixed accepted predecessor is `3c320ddee4beee08f5a055c765f4e230a6d1dd81`.
The tool verifies the four installed hashes against that approved manifest or
the selected target hashes. It verifies baseline Git objects, the exact clean
target checkout, target ancestry on `origin/master`, its own committed source
and the module bundle hash. Unknown installed edits are refused. No secret,
credential, database, execution history or normal user data is copied into Git
or included in the plan.

Prepare a new immutable checkout on home-core from the pushed commit. This only
prepares Git source; it does not run the full host release or change its deployed
revision/current pointer. Never reuse or clean a dirty existing release directory.
Use the already verified SSH host key and non-interactive sudo:

```sh
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=8 home-core
presentation_revision=FULL_40_CHARACTER_PUSHED_SHA
presentation_release=/srv/homecompute/releases/$presentation_revision
sudo -n git clone --no-checkout https://github.com/madslundt/HomeCompute.git "$presentation_release"
sudo -n git -C "$presentation_release" fetch origin master
sudo -n git -C "$presentation_release" checkout --detach "$presentation_revision"
sudo -n python3 "$presentation_release/scripts/setup-notification-presentation.py" --source "$presentation_release" --revision "$presentation_revision"
```

If that clean exact checkout already exists, inspect and use it without cloning
again. The default command above only reads source, installed files, unit state
and aggregate in-flight counts. Review its four module before/after hashes,
bundle hash, preserved artifacts and exact previously active restart list. Its
`plan_sha256` binds all these details. A later apply rechecks the plan under its
exclusive lock and refuses any change:

```sh
sudo -n python3 "$presentation_release/scripts/setup-notification-presentation.py" --source "$presentation_release" --revision "$presentation_revision" --apply --expected-plan-sha256 REVIEWED_PLAN_SHA256
```

The tool backs up original module bytes and the public plan under root-only
`/var/lib/homecompute-notification-presentation/backups/`. It pauses only active
consumers of cached renderers, replaces the four files atomically while preserving
their original ownership/modes, verifies every target hash, and restores only
previously active consumers. The adapter caches machine/task renderers. Its
existing Wants/After dependency invokes `homecompute-openclaw-machine-status.service`
when the adapter starts. The plan explicitly declares this read-only status
projection, requires it to be idle beforehand, and verifies it finishes inactive;
the existing status timer remains unchanged. Access
backend restart also requires preserving the previous activity of its proxy and
certificate watcher; a running certificate reload blocks the refresh. Other inactive
services stay inactive. The retired Telegram receiver, task-feed, reminder timers,
location reminders, site workers, all unit definitions and all private state stay
under their existing lifecycles. Reminder workers load their renderer on their
next existing run; the location worker imports it for each delivery.

In-flight notification sends, running turns or unsettled access/transaction
operations block the refresh. Existing pending incidents and uncertain completed
turns are preserved; never clear them to force an update. On a write or restart
failure, including KeyboardInterrupt, SIGINT, SIGTERM or SIGHUP during mutation,
the tool restores original source and prior consumer activity, then restores the
original signal handlers. Uncatchable SIGKILL or power loss requires operator
reconciliation from the retained backup/plan. If an
unexpected concurrent edit prevents safe rollback, it preserves that edit, leaves
paused consumers stopped and reports the backup path for operator reconciliation.
It does not overwrite unknown bytes during rollback.

Success means all four installed hashes match the selected committed sources,
the prior active service set is restored, and the plan's preserved artifacts are
unchanged. The preservation check includes the six unrelated stale communication
bundle files, all attached OpenClaw units/masks, installed public configuration,
proxy configuration, tmpfiles and the active monitoring policy. The six files are
`docs/openclaw-operations.md`, `docs/openclaw-tools.md`,
`docs/homecompute-operations.md`, `docs/openclaw-integrations.md`,
`config/system-monitoring.json` and `scripts/openclaw-machine-snapshot.py`, relative
to `/opt/homecompute/openclaw-communication`. Review the returned file hashes and
service state, then rerun the default plan: all four modules should say
`changed: false`. Check the adapter, access backend/proxy/watcher and preexisting
timers with `systemctl show`; retain the existing Telegram mask. Do not send a
live notification merely to validate presentation. Fixtures cover rendering;
runtime delivery remains under the existing admission/deduplication policy.

Local validation:

```sh
python3 tests/notification-presentation-refresh-test.py
python3 tests/openclaw-access-notifications-test.py
python3 tests/openclaw-machine-notifications-test.py
python3 tests/openclaw-household-tasks-test.py
python3 tests/openclaw-communication-test.py
python3 tests/openclaw-task-feed-test.py
git diff --check
```
