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

## Deploy after merging

```bash
./scripts/homecompute deploy all
```

This resolves `origin/master` once and pins both deployments to that same SHA.
It checks SSH reachability and host prerequisites on every selected target
before changing the first host. `all` deploys `home-spark` first, then
`home-core`: the existing gateway remains unchanged if Spark fails. It stops
after the first failed deployment. There is no automatic rollback of application
data or model artifacts, and no automatic cross-host route smoke test; each
host's existing deployment health checks must pass. Run `doctor` and verify the
actual client route after a rollout.

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
