# OpenBot HTTPS monitoring and Caddy CA recovery

Home Core runs an hourly HTTPS check inside the OpenBot container using the
bundled Bun runtime and the deployed agent CA bundle. The request is a
credential-free GET to the private LiteLLM model-list endpoint. A dedicated
SSH key stays on Home Core. Its guest authorization accepts only the fixed
probe from the Home Core bridge address, with SSH forwarding disabled.

A failed check opens an incident and sends one push-enabled alert to the
owner's Home Assistant Inbox using the existing Inbox webhook secret.
A failed delivery is retried with the same Inbox external ID. A healthy
check closes the incident after any pending notification is delivered.

A separate timer at 06:15 Copenhagen time copies the Caddy root and intermediate
certificates and their signing keys directly into the existing encrypted
selected-state Restic repository. The job reads back all four files and compares
them to their source before recording success in
`/var/lib/homecompute/nas-recovery-backup/last-caddy-ca-backup.json`.
Its snapshot tag is `homecompute-caddy-ca`. The original authorization marker,
NAS mount validation, and repository password remain in use. Restic's regular
retention groups these snapshots by their separate paths.

## Deploy and verify

From a clean checkout of the exact published `origin/master` revision on the
operator workstation, run:

```sh
./scripts/setup-certificate-maintenance.sh deploy
```

The installer activates both timers and queues the first runs. Inspect their
schedules and latest service results on Home Core:

```sh
systemctl list-timers homecompute-openbot-tls-monitor.timer homecompute-caddy-ca-backup.timer
journalctl -u homecompute-openbot-tls-monitor.service -u homecompute-caddy-ca-backup.service -n 30
```

For recovery, select a verified `homecompute-caddy-ca` snapshot and restore the
four files to a new root-only scratch directory. Verify their ownership,
permissions, and cert/key pairs before copying them into the Caddy CA directory
with Caddy stopped. Preserve the current files for rollback. Restore the
original root pair together, then verify the gateway from OpenBot after Caddy
restarts. Keep both the Restic password and restored private keys out of logs
and Git.

To suspend the checks, stop the two timers. To disable them across reboots,
also remove their exact links in `/etc/systemd/system.attached/timers.target.wants`
and reload systemd. Existing snapshots are retained. Reapply the published
installer to restore the schedules.
