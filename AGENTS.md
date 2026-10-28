# HomeCompute agent instructions

Applies to this entire repository. These repository-specific instructions take
precedence over generic parent-directory workflows. HomeCompute is infrastructure,
not a root npm application; use the tools and checks declared here. Keep changes
focused on the requested outcome.

## Start with the relevant source

- Read [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md) before
  changing implementation or deployment behavior.
- For host access, deployment, rollback, or drift, read
  [HomeCompute operations](docs/homecompute-operations.md). Use
  [Git deployment](docs/git-deployment.md) for release internals and recovery.
- For NixOS or Home Manager changes, read
  [NixOS operations](docs/nixos-operations.md).
- For a workload, read its `deploy/<workload>/README.md`, relevant `config/`
  definitions, setup scripts, and tests. For workflows, start with
  [automations/README.md](automations/README.md).
- For architecture, model selection, or trust-boundary changes, read the relevant
  [ADRs](docs/adr/README.md) and [documentation guide](docs/README.md).
  Dated plans and research describe evidence at that time, not current live state.

## Git is the source of truth

Every persistent HomeCompute-managed change must be reproducible from tracked
source plus explicitly external secrets and durable data. This includes host and
guest configuration, containers, services, scripts, agent policies, workflows,
image/model pins, network rules, and persistent application settings. A host edit,
UI change, runtime override, or maintenance receipt alone is not the desired state.

1. **Inspect before changing.** Run `git status --short --branch` and inspect the
   relevant diffs. Preserve existing staged, unstaged, and untracked work. Never
   reset, clean, overwrite, or commit unrelated changes to obtain a clean tree.
   For live work, establish the deployed revision and actual affected service
   configuration before choosing what to change.
2. **Implement in the repository first.** Edit the owning declaration or add an
   idempotent installer/patch builder when an application requires API or UI
   configuration. Capture non-secret settings and the procedure in source;
   reference externally provisioned credentials by name or path. Update related
   tests and operational documentation in the same change.
3. **Validate, then publish for deployment.** Review the exact diff, run applicable
   checks, and commit/push only the intended files within the authorized scope.
   Production releases use a reviewed commit available on `origin/master`, or an
   explicitly selected historical full SHA. Uncommitted files and local-only
   commits are not deployable releases. A source-only task may end with a reviewed
   patch; state clearly that it is uncommitted or undeployed.
4. **Apply through the owning lifecycle.** Use
   `./scripts/homecompute deploy <home-core|home-spark|all> --revision FULL_SHA`
   for a normal authorized release. Use the workload's documented guarded setup
   command for services outside that release wrapper. Execute repository-owned
   tooling from the selected release; do not leave hand-copied fixes in hosts,
   guests, containers, `/etc`, systemd overrides, or application UIs. `git pull`
   alone is not deployment. This policy does not itself authorize live changes.
5. **Verify actual behavior.** After a rollout, inspect `status`, `services`,
   `doctor`, and `drift` through `./scripts/homecompute`, then check the affected
   service's image/configuration and authenticated client path using its runbook.
   Compare generated/installed files with their source or rendered output without
   exposing secrets. A matching revision marker proves only the recorded checkout:
   Spark's wrapper does not reconcile every model, speech, or adapter project.
6. **Close the loop.** Account for every persistent change made during the task:
   source path, applied revision or dedicated lifecycle, verification, and any
   remaining divergence. Report unknown or inaccessible checks as unverified.
   Never call systems synchronized based only on a successful command or marker.

## Drift and incident repairs

- When live state differs from source, inspect and preserve sanitized evidence
  before replacing it. Determine whether it is an intended pending change, an
  incident workaround, or unintended drift. Do not blindly overwrite live state
  or import it as desired configuration.
- Reconcile approved changes into their owning source, validate and publish them,
  then apply the normal lifecycle. Restore unintended drift from the selected
  known-good release when authorized, preserving application data.
- An urgent repair may use a direct change when necessary within an authorized
  incident response. Capture the equivalent repository change in the same task,
  reapply through the normal lifecycle, and remove superseded overrides after
  verification. If reconciliation is blocked, report the exact outstanding
  divergence, recovery steps, and rollback; the repair is temporary.
- Maintenance tools can leave active image/configuration overrides outside Git.
  Reconcile selected digests/settings into source and retire the override through
  its documented lifecycle. Respect deployment guards that detect pending
  overrides; never delete their markers just to bypass a deployment refusal.
- Keep planned differences explicit: staged candidates, deliberately stopped
  services, and vendor-managed components require their own lifecycle and
  evidence. Preserve qualification gates rather than starting everything in
  Compose or promoting a model to make inventories appear equal.

## Ownership and boundaries

| Concern | Authoritative source |
| --- | --- |
| NixOS host services, networking, users, storage, backups | `hosts/`, `modules/nixos/`, `flake.nix`, `flake.lock` |
| User environment, activated with NixOS | `home/`; no standalone Home Manager activation |
| Containers and runtime wiring | `deploy/`, non-secret `config/` templates, owning setup scripts |
| Text artifacts and capability routes | `config/model-catalog.json`, `config/capability-routes.json` |
| GB10 artifact staging inventory | `config/gb10-model-roster.json`; membership does not grant production promotion |
| Workflow and agent behavior | `automations/`, `deploy/openclaw/`, relevant `config/` and `scripts/` |
| Encrypted secrets | `secrets/*.sops.yaml`, `.sops.yaml`; plaintext and private identities stay external |

- Change canonical inputs before generated output. For LiteLLM `model_list`, use
  `python3 scripts/model_registry.py render` and `check`; review the generated
  diff. Authentication, privacy, and network settings remain explicit policy.
- Keep persistent non-secret settings in declarations, not only in live env files.
  Keep credentials, real workflow exports, private payloads, databases, model
  weights, and `/srv/state` data outside Git. Use sanitized templates or patch
  builders for workflows and documented backup/restore for durable state.
- Spark's DGX OS, drivers, CUDA, firmware, and NVIDIA Container Toolkit remain
  vendor-managed. Record required versions and reproducible maintenance steps;
  repository deployment does not replace vendor administration or data backups.
- Preserve the local-first security model: approved LAN/Tailscale access, scoped
  credentials, private-data separation, and Home Assistant action authority.
  Qualification and access approvals remain prerequisites for promotion/exposure.

## Validation and handoff

- Run `git diff --check` for every change; review staged changes separately when
  present. For documentation-only edits, verify referenced paths and commands.
- For implementation/configuration changes, run focused tests from `tests/` and
  `./scripts/validate-repository.sh`. Follow CI in
  [.github/workflows/validate.yml](.github/workflows/validate.yml) for additional
  checks, including `nix flake check --no-build` for Nix changes. Git-backed flakes
  require new imported files in the index; stage only the intended files.
- Report missing tools, skipped checks, and pre-existing failures accurately.
  Keep synthetic test evidence separate from live readiness claims.
- For live changes, retain the prior release and use the workload's backup,
  health, and rollback procedure. Code rollback does not undo data migrations.
- Finish with what changed, checks and results, publication/deployment status,
  and any remaining drift or unverified systems. A documentation update alone
  does not establish that existing systems are synchronized.
