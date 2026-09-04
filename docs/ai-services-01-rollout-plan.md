# `ai-services-01` rollout plan

**Target:** GMKtec K15, NixOS 26.05
**Decisions:** [ADR-016](adr/016-nixos-control-plane-host.md), [ADR-017](adr/017-consolidated-application-host.md)

This is the execution sequence between "NixOS is installed" and "workloads are
running". It exists because that sequence was previously spread across three
documents, each correct for its own purpose:

- the [installation runbook](nixos-install-runbook.md) gets NixOS onto the disk;
- the [control-plane plan](nixos-control-plane-node-plan.md) defines the
  configuration boundary and acceptance criteria;
- the [platform execution plan](platform-execution-plan.md) orders work across
  both physical nodes.

This document orders the stages for this one host and names what blocks each.
Where it disagrees with an older document, the older document is wrong and
should be reconciled.

**Position as of 2026-09-04:** stage 0 not started. Hermes deferred by owner
decision. Only stage 1 of ADR-017's kernel plan — containers on the host
kernel — is in scope.

## Stage summary

| # | Outcome | Blocked by | Exit gate |
| --- | --- | --- | --- |
| 0 | Reachable, hardened host | SSH key passphrase; hardware config from the real machine | `ssh ai-services-01` succeeds while the console is still attached |
| 1 | Secrets materialize | Age identity created and backed up offline | Six secrets under `/run/secrets`, none in the Nix store |
| 2 | Gateway runs | Three reviewed image digests; live AI Home inventory | Only Caddy publishes, on `127.0.0.1`; no secret in rendered Compose |
| 3 | Off-host backup | Stage 1; an initialized Restic repository | Isolated restore of `/srv/state` passes |
| 4 | Real network topology | Observed NIC names and addresses | Allowed *and denied* paths verified after the change |
| 5 | n8n on this host | Stages 1–4; HAOS workflow inventory | No duplicate scheduled runs; restore tested |
| 6 | Hermes | Deferred; `agents` microVM before real data | Out of scope until reopened |

## Before touching hardware

Two inventories gate stages 2 and 5, cost nothing, and can be done now:

1. **The live AI Home control plane.** Which services are actually deployed,
   which images and keys are in use, what the existing LiteLLM routes today.
   R-021 exists because inferring this from source has already been identified
   as a trap. Stage 2's equivalence gate is unmeasurable without it.
2. **The HAOS n8n instance.** Every workflow, its schedule, its credentials,
   and its owner. Stage 5 cannot prove "no duplicate runs" against an unknown
   baseline.

Neither requires the K15. Do them while waiting.

## Stage 0 — Install

Follow the [installation runbook](nixos-install-runbook.md). Your prerequisites:

- Add a passphrase to the admin key: `ssh-keygen -p -f ~/.ssh/id_ed25519_ai-services-01`.
  `mads` holds passwordless sudo, so this key is root-equivalent.
- Regenerate the hardware configuration on the machine and merge **only**
  `boot.initrd.availableKernelModules` and `boot.initrd.kernelModules`. The
  repository mounts by label deliberately; do not import the detected
  UUID-based `fileSystems` block.
- Decide swap. `swapDevices = [ ]` is defensible at 48 GB; zram is the cheap
  alternative. Record the choice rather than leaving it implicit.

**Exit gate:** `ssh ai-services-01` succeeds from the workstation *before* the
keyboard and monitor are disconnected. Password and root login are disabled, so
a failure here means a reinstall.

## Stage 1 — Secrets

Everything else that touches credentials depends on this, including backups.

1. Create the age identity, install it at `/var/lib/sops-nix/key.txt`, and back
   it up offline. Losing it makes every encrypted file in the repository
   unreadable; `generateKey = false`, so nothing will silently recreate it.
2. Add the public recipient to `.sops.yaml` (neither that file nor `secrets/`
   exists yet — both are created here).
3. Encrypt `secrets/ai-services-01.sops.yaml` containing all six values:
   `control-plane/compute-api-key`, `control-plane/litellm-master-key`,
   `control-plane/litellm-salt-key`, `control-plane/postgres-admin-password`,
   `control-plane/postgres-app-password`, and `restic/password`.
4. Set `homecompute.secrets.defaultSopsFile` and flip `enable = true`.

The five control-plane secrets are group-readable by `homecompute-secrets`
(GID 989, fixed in `modules/nixos/storage.nix` and matching
`CONTROL_PLANE_SECRET_GID`). `restic/password` is mode `0400` and root-only.

**Exit gate:** all six materialize under `/run/secrets`, and none appears in the
Nix store, logs, or rendered Compose output.

## Stage 2 — Gateway

1. Review and pin the three `REPLACE_WITH_*` digests in
   `config/control-plane.env.example`. Validation fails while they are
   placeholders — that is intentional, not an obstacle to work around.
2. Render before starting:
   `docker compose --env-file … -f deploy/control-plane/compose.yaml config --quiet`.
3. Start it. Durable data stays below `/srv/state/control-plane`.

**Exit gate:** only Caddy publishes a port, bound to `127.0.0.1`; LiteLLM and
PostgreSQL publish nothing; no secret value appears in rendered output; gateway
equivalence and rollback pass against the inventory taken above.

The existing service keeps running until this passes. Do not decommission it.

## Stage 3 — Backups

`services.restic.backups.homecompute` sets `initialize = false`, so the
repository must exist before the first run — the module will not create it.

1. Provision the off-host repository and initialize it manually.
2. Set `repository`, `passwordFile` (pointing at the stage 1 secret), and
   `prepareCommand`. All three are asserted; activation fails without them.
3. `prepareCommand` must produce an application-consistent PostgreSQL dump or
   snapshot. A file-level copy of a running database is not a backup.

**Exit gate:** an isolated restore of `/srv/state` succeeds — restored
somewhere that is not this host, and actually opened. A backup that has never
been restored is an assumption.

This gates migrating any real workload, not the gateway standing up.

## Stage 4 — Network

The initial configuration is DHCP so it does not depend on unverified NIC
names. Replace it once the machine is racked and the interfaces are known.

1. Explicit `systemd-networkd` configuration using observed names and addresses.
2. Assign the second 2.5GbE port as the private, non-routed `ai-compute-01`
   link. Only LiteLLM may use it.
3. Add only the required ingress to the firewall.

**Exit gate:** allowed paths work *and* denied paths fail, verified after the
change rather than assumed from the configuration.

## Stage 5 — n8n migration

The first workload to exercise ADR-017's isolation controls in anger.

1. New Compose project with its own Docker network, non-root runtime user, no
   Docker socket, `/srv/state/automation` subtree, distinct sops group, and
   memory and CPU limits set from measured gateway and database usage.
2. Add `automation` to `expected_deployment_projects` in
   `scripts/validate-repository.sh`. Validation will fail until you do. That is
   the check forcing an isolation review, not an obstruction — see URS-PA-021.
3. Import one sanitized workflow, then one low-risk real workflow.
4. Soak. Disable the HAOS scheduler only after the new job has run cleanly for
   its agreed period.

**Exit gate:** no workflow runs twice, credentials are isolated from the
gateway's, and a restore of the workflow database has been tested.

Browser workers are not part of this stage. When they appear they require the
`automation` microVM first, because rendering arbitrary web content on the
gateway's kernel is precisely the case URS-PA-020 names.

## Stage 6 — Hermes

Deferred. Retained here so the preconditions are not rediscovered later.

Keeping the agent layer absent is the largest available risk reduction on a
consolidated host: it removes the prompt-injectable component that holds tool
credentials. R-043 is scored for that absence and R-043a is the trigger to
re-score.

When reopened: pin the Hermes/NemoClaw/OpenShell tuple, run one synthetic-data
`owner` sandbox, qualify it through `ai.home.arpa` at 64K context, **then**
build the `agents` microVM before any real or unauthored data reaches it, and
only then add `partner` and `family`.

## Cliff edges

Failure modes that have already cost time, or would:

- **Flakes ignore untracked files.** A new or modified `.nix` file that is not
  staged is invisible to the build, which silently uses the committed version.
  Check `git status` before every `nixos-rebuild`.
- **The ESP fills.** `configurationLimit = 10` bounds it at roughly 1 GB, which
  is why the runbook specifies 2 GiB. A full ESP blocks the rebuild that would
  fix it.
- **The age identity is single-point-of-failure.** Back it up offline before
  encrypting anything you cannot reconstruct.
- **Restic will not initialize the repository for you.**
- **Disabling the old scheduler early** produces missed jobs rather than
  duplicated ones, which is harder to notice.
- **`deploy/` project registration fails closed.** Expect it at stage 5.
