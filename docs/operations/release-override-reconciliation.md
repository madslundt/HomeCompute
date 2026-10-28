# Reconcile maintained images before a full release

The deployment wrapper refuses active Docker and Homepage overrides. Do not
remove their markers to force a release. First inspect the real containers,
preserve the maintenance records, and select the already approved running
versions in the canonical non-secret environment templates.

The October 28 reconciliation retains n8n 2.46.0, Homepage 2.7.0 and Mattermost
11.11.3 at their observed immutable digests. Homepage's active services file
differs from the published source only in the Spark-model icon; that choice is
now captured in source too. No new application version or migration is selected.
Spark's running 9 GiB KV-cache setting is already captured in the published
registry, roster, setup validator and tests; keep its local patch as evidence
until its older source checkout is safely updated.

`scripts/reconcile-release-overrides.py` is an operator-only lifecycle for these
three services. It requires a clean, root-owned exact release on `origin/master`
at `/srv/homecompute/releases/FULL_SHA` and runs only as root on home-core.
It has no agent or network-facing entry point. The default only reads and plans:

```sh
sudo python3 /srv/homecompute/releases/FULL_SHA/scripts/reconcile-release-overrides.py \
  --revision FULL_SHA
```

The plan checks each canonical image against the actual healthy container and
installed image, then binds container IDs, configuration hashes, source files,
maintenance records and marker hashes. Historical markers must have a complete
image-successor chain to the running source-pinned image. Uncertain records,
unreviewed services, changed mounts, symlinks and unmatched Homepage content
are refused. No private environment values or backup contents are printed.

After reviewing the exact plan, archive its guards through the same source:

```sh
sudo python3 /srv/homecompute/releases/FULL_SHA/scripts/reconcile-release-overrides.py \
  --revision FULL_SHA --expected-plan-sha256 REVIEWED_PLAN_SHA256 --apply
```

Apply holds the normal deployment lock and Docker-maintenance operation lock
and rechecks the plan and live identities. Mattermost's separate persistent
`runtime.env` is also fenced: only its single image-pin line is reconciled to
source after checking the retained maintenance baseline. Its exact private
preimage, ownership and mode are retained, and every other byte is preserved.
The service is not restarted by this step. Apply moves only verified markers into
`/var/lib/homecompute/reconciled-overrides/PLAN_SHA256/`. Its private receipt
retains the reviewed inputs; original operation records, rollback containers,
application backups and Homepage override contents stay in place. A failed
rename or changed container restores the moved guards. Interrupted retirement
leaves the receipt and remaining guards for inspection; never clear those
remaining guards manually. An already reconciled zero-marker plan is a no-op.

Archiving a guard does not deploy a service or claim synchronization. Run the
normal selected-revision release immediately afterward:

```sh
./scripts/homecompute deploy home-core --revision FULL_SHA
```

Compare the resulting container images and mounted configuration with this
release, verify authenticated application paths, and inspect `status`,
`services`, `doctor` and `drift`. Preserve intentionally stopped workloads and
qualification gates. Keep the reconciliation receipt and all prior release,
container and data backups. If deployment fails, stop and inspect its partial
state before recovery; a code rollback must not downgrade a database or pretend
to reverse a migration. The archived preimages remain available for a reviewed
restoration if the prior maintained deployment must be selected again.

Spark deployment is separate. Preserve its six-file local KV-cache patch before
fast-forwarding its old checkout; do not reset or clean it. The normal release
still requires the operator's interactive sudo authentication and intentionally
does not start the stopped text baseline or reconcile vendor OS/driver packages.
