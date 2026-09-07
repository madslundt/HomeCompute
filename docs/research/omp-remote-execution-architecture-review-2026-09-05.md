# OMP remote-execution architecture review

**Date:** 2026-09-05
**Scope:** MacBook → SSH over Tailscale → `home-core` (NixOS) → tmux → Oh My Pi (OMP) → repositories
**Verdict:** Adopt, with a non-admin execution account and explicit recovery/backup rules. The topology solves MacBook shutdown and SSH-disconnect continuity. It does not make an interactive OMP turn survive a `home-core` reboot, and the current `mads` account is too privileged for broadly unattended agent execution.

## Decision in one diagram

```text
MacBook (terminal only)
  └─ ordinary OpenSSH carried over Tailscale
       └─ home-core (always on, NixOS)
            ├─ dedicated unprivileged agent account (recommended)
            │    ├─ tmux server
            │    │    ├─ one OMP session per task/worktree
            │    │    └─ interactive approvals and observation
            │    ├─ local repository clones/worktrees
            │    └─ ~/.omp session/auth/history state
            └─ mads/admin account
                 └─ reviewed deployment and nixos-rebuild only
```

The words “SSH/Tailscale” should mean **ordinary key-authenticated OpenSSH over the Tailscale interface** for the first deployment. That is already how this repository is configured: OpenSSH remains the server, while the firewall admits port 22 on `tailscale0`. Tailscale SSH is a different optional mode that intercepts tailnet port 22 and uses separate tailnet SSH rules; it is not required for this design ([Tailscale SSH](https://tailscale.com/docs/features/tailscale-ssh)).

## What the design actually guarantees

| Event | Result |
| --- | --- |
| MacBook sleeps, shuts down, or loses its network | OMP continues on `home-core`, because the OMP process and repository are remote. |
| SSH connection drops | OMP continues if it is inside tmux. tmux explicitly keeps programs running after its client detaches and supports later reattachment ([tmux Getting Started](https://github.com/tmux/tmux/wiki/Getting-Started)). |
| MacBook reconnects | SSH back to `home-core`, then attach to the named tmux session. |
| `home-core` reboots, loses power, or the tmux server dies | The live OMP process stops. tmux is a process multiplexer, not a reboot-persistent job engine. |
| OMP is restarted after interruption | The conversation can be reopened from OMP's on-disk session. Current OMP stores sessions below `~/.omp/agent/sessions/<encoded-cwd>/`, exposes `--resume`, and records interrupted turns as aborted when reconstructed ([OMP session model](https://github.com/can1357/oh-my-pi/blob/main/docs/session.md), [OMP README](https://github.com/can1357/oh-my-pi#install)). This is transcript recovery, not automatic continuation of the interrupted tool call or turn. |
| OMP requests approval or asks a question while unattended | The process remains alive but work can wait indefinitely for input. tmux solves process continuity, not interaction requirements. |

The proposal is therefore the right solution for **interactive, long-running coding sessions that must outlive the laptop connection**. It should not be described as durable job execution across server reboots.

## Fit with the current repository and host

The planned pieces already exist:

- `home-core` is the accepted consolidated NixOS application host ([ADR-016](../adr/016-nixos-control-plane-host.md), [ADR-017](../adr/017-consolidated-application-host.md)). The rollout record says NixOS, Home Manager, OpenSSH, tmux, and Tailscale are active, and records the source checkout at `/home/mads/HomeCompute` ([rollout record](../home-core-rollout-plan.md)).
- Home Manager already enables tmux for `mads` ([tmux module](../../home/mads/tmux.nix)).
- OpenSSH already disables password and keyboard-interactive authentication, forbids root login, and permits only `mads` ([SSH module](../../modules/nixos/ssh.nix)). NixOS supports declarative OpenSSH and authorized keys as used here ([NixOS manual](https://nixos.org/manual/nixos/stable/#sec-ssh)).
- The host firewall admits TCP 22 and 443 on `tailscale0` ([firewall module](../../modules/nixos/firewall.nix)).
- OMP officially supports Linux, provides a Nix flake package and Home Manager module, and publishes `linux-x64`, so there is no platform mismatch with this x86-64 NixOS host ([OMP README](https://github.com/can1357/oh-my-pi#install)). A pinned flake/Home Manager input fits this repository better than an imperative curl or floating profile install.

Two access details remain to verify before calling the path hardened:

1. The repository intentionally does not contain a live tailnet policy. Confirm a deny-by-default grant allows only the administrator identity/device posture to reach `home-core` TCP 22. Tailscale recommends grants and supports port-scoped access ([Tailscale grants](https://tailscale.com/docs/features/access-control/grants), [local access policy](../access-policy.md)).
2. LAN SSH is still explicitly open on `enp44s0` in the host module ([host configuration](../../hosts/home-core/default.nix)). That was introduced for bootstrap and means access is **not currently Tailscale-only**. Keep it only if LAN SSH is an intentional recovery path; otherwise remove it in a separately reviewed NixOS change after console/Tailscale recovery has been tested.

For an unattended tagged server, Tailscale documents disabling node-key expiry as an availability option, with the trade-off that a stolen device remains trusted until revoked. The repository's policy already limits that exception to tagged unattended servers ([Tailscale key expiry](https://tailscale.com/docs/features/access-control/key-expiry), [local access policy](../access-policy.md)). The existing exit-node and `192.168.30.0/24` subnet advertisements are not needed for direct SSH to `home-core`; direct tailnet addressing or MagicDNS is the smaller dependency surface.

## Main risk: OMP must not run as the current admin identity unattended

`mads` is the only allowed SSH account and has passwordless sudo. ADR-016 correctly treats its private SSH key as root-equivalent ([ADR-016](../adr/016-nixos-control-plane-host.md)). OMP is a host-capable coding agent: its shell and tools can change files and execute programs, and its configurable `yolo` mode auto-approves execution. OMP itself says its bash interception/pattern layer is a best-effort capability preference rather than an execution-security boundary ([OMP approval modes](https://github.com/can1357/oh-my-pi/blob/main/docs/approval-mode.md), [OMP bash policy](https://github.com/can1357/oh-my-pi/blob/main/docs/tools/bash.md)).

Running unattended OMP as `mads` therefore lets a mistaken or prompt-injected command reach root via `sudo`, and places the agent in the same failure domain as n8n, the gateway, `/srv/state`, and materialized secrets. tmux changes none of that.

Recommended boundary:

- Add a dedicated local account such as `omp` in a future reviewed NixOS change: no sudo, no Docker group/socket, no access to `/run/secrets` or other workloads' `/srv/state`, and write access only to its own home and repository/worktree roots.
- Keep system activation under `mads`: the agent may propose changes to the Git checkout, but a human reviews and applies `nixos-rebuild` or production Compose operations.
- Use OMP's approval policy for ergonomics and defense in depth, including explicit denies for `sudo`, host shutdown, Docker control, and production deployment. Do not treat those patterns as the OS security boundary.
- If the agent genuinely needs risky tools or untrusted browser/content processing, move that work into the `agents`/toolbox microVM boundary already anticipated by ADR-017 rather than granting the OMP user host privilege.

This is the most important change to the original proposal. If creating an account is deferred, use `mads` only for attended sessions with conservative approvals; do not call that an unattended agent host.

## Repository and session layout

The repositories must live on `home-core`; mounting or editing a sleeping MacBook's filesystem would reintroduce the original dependency. Use one stable clone for serial work and one Git worktree per concurrently running top-level OMP session. Multiple OMP processes editing the same working tree can race on files, index state, tests, and branch changes. OMP's built-in `task` workers provide their own isolated worktrees, but separately launched top-level OMP instances do not gain isolation merely because they occupy different tmux panes ([OMP README, subagents](https://github.com/can1357/oh-my-pi#05--first-class-subagents)).

OMP state is local to the account and host. Its documented default includes:

- session JSONL below `~/.omp/agent/sessions/`;
- content-addressed blobs below `~/.omp/agent/blobs/`;
- prompt history in `~/.omp/agent/history.db`;
- provider credentials resolved from runtime values, config, stored credentials, or environment variables ([OMP session model](https://github.com/can1357/oh-my-pi/blob/main/docs/session.md), [OMP providers](https://github.com/can1357/oh-my-pi/blob/main/docs/providers.md)).

Treat `~/.omp` as private state: owner-only permissions, no Git inclusion, and an encrypted off-host backup if resuming sessions after disk loss matters. Session logs can contain source, prompts, command arguments, and outputs. Prefer OMP's credential store or runtime secret material over literal keys in `models.yml`, and never put provider secrets in repository configuration.

## Minimal operating pattern

After the account, pinned OMP package, tailnet grant, and local checkout have been created declaratively and verified:

```bash
# From the MacBook: ordinary OpenSSH transported over Tailscale.
ssh omp@home-core

# On home-core: attach to the named session or create it.
tmux new-session -A -s omp-homecompute

# Inside tmux.
cd /home/omp/src/HomeCompute
omp
```

Detach with tmux's default `Ctrl-b d`; later repeat SSH and `tmux attach-session -t omp-homecompute`. Name sessions by task or repository, and list them with `tmux list-sessions`. These attach/detach semantics are the intended tmux use case ([tmux Getting Started](https://github.com/tmux/tmux/wiki/Getting-Started)).

Before relying on the setup, run four acceptance tests:

1. Start a harmless, multi-minute OMP task in tmux, shut down the MacBook, reconnect from another approved tailnet client, and verify the same live TUI/process.
2. Verify TCP 22 succeeds for the intended admin client and fails for an unapproved tailnet identity/device; separately decide and test whether LAN SSH is retained.
3. Reboot `home-core` intentionally, verify that tmux/OMP stopped, then use OMP resume and confirm the interrupted turn is reported as aborted rather than assumed complete.
4. From the agent account, prove `sudo`, Docker control, `/run/secrets`, other `/srv/state` trees, and unrelated repository roots are inaccessible.

## Alternatives

| Alternative | Assessment |
| --- | --- |
| `mosh` instead of SSH | Helpful for roaming and lossy links, but it does not solve MacBook shutdown or server reboot. tmux remains the persistence layer. It also adds another server/UDP access path, so it is unnecessary initially. |
| OMP `/collab` browser link | Useful for sharing or browser access, and OMP encrypts live session payloads; however, possession of the full link permits reading and steering the session, and the host OMP process must still be alive ([OMP collaboration docs](https://github.com/can1357/oh-my-pi/blob/main/docs/collab.md)). It is not a replacement for the private management path. |
| `nohup`/background OMP | Weaker operational UX for an interactive TUI: no straightforward reattachment, approval handling, or live screen. tmux is the better fit. |
| systemd service | Better for a bounded, noninteractive job that must restart at boot, but a poor wrapper for the interactive OMP TUI. Use a purpose-built noninteractive runner and explicit idempotency/checkpointing if reboot-persistent automation becomes a requirement; NixOS manages system services through systemd ([NixOS manual](https://nixos.org/manual/nixos/stable/#sec-systemctl)). |
| Keep OMP on the MacBook and sync repositories | Does not meet the availability requirement; syncing also creates split session/auth state and potential Git conflicts. |
| Run OMP on `home-spark`/GB10 | Conflicts with the accepted inference-appliance boundary; `home-core` is the correct application/agent machine ([ADR-017](../adr/017-consolidated-application-host.md)). |

## Final recommendation

Proceed with the proposed architecture as the **interactive remote-agent baseline**, expressed more precisely as:

> MacBook → OpenSSH over Tailscale → `home-core` NixOS → unprivileged OMP account → tmux → one OMP session per repository worktree.

Do not make `mads` + passwordless sudo + unattended/auto-approved OMP the steady state. Pin OMP through the Nix/Home Manager graph, verify the tailnet grant and deliberate LAN-recovery choice, keep repositories and OMP state local to `home-core`, back up the private OMP state, and document that reboot recovery is manual session resume rather than continuous execution.
