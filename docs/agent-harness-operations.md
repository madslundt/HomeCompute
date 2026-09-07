# Agent harnesses on `home-core`

Pi is the default harness on `home-core`; OMP is installed alongside it for
tasks that benefit from integrated LSP, debugging, model routing, or subagents.
Codex remains a supported option and the Responses-API and sandbox reference;
it is not an exclusive gateway to the developer workflow. Harnesses used on
`home-core` run under the unprivileged `agent` account. The account has no sudo
or Docker access and cannot read production secrets or state. `mads` remains
the human administration account.

## Connect and start a session

The MacBook launcher connects over SSH and creates or reattaches the matching
tmux session:

```bash
home-core-agent                 # interactively choose Pi or OMP
home-core-agent pi              # Pi in HomeCompute
home-core-agent omp             # OMP in HomeCompute
home-core-agent pi OtherRepo    # Pi in ~/src/OtherRepo
home-core-agent --list          # repositories and running sessions
```

The optional third argument overrides the tmux session name. The client script
is `scripts/home-core-agent.sh`; on this MacBook it is linked as
`~/.local/bin/home-core-agent`. The remote `agent-session` command is installed
declaratively.

From the MacBook, use ordinary OpenSSH over Tailscale with the dedicated
home-core key:

```bash
ssh -i ~/.ssh/id_ed25519_ai-services-01 agent@home-core
```

On `home-core`, clone repositories below `~/src`. Use a named tmux session and
one Git worktree for every concurrently running top-level session:

```bash
tmux new-session -A -s pi-homecompute -c ~/src/HomeCompute pi
```

Use `omp` to start the feature-rich comparison harness. Do not run both against
one worktree concurrently.

Detach with `Ctrl-b d`. Reconnect and attach with:

```bash
tmux attach-session -t pi-homecompute
```

tmux keeps the harness alive across SSH disconnects and MacBook shutdowns. It does not
survive a `home-core` reboot. After a reboot, start tmux again and use
the selected harness's resume command to reopen the persisted session; verify
any interrupted action before continuing it.

## Authentication

Do not copy provider tokens into this repository. Authenticate each harness as
the remote `agent` user. For OAuth flows that need the MacBook browser, OMP supports
running the login on the remote host through SSH:

```bash
omp auth-broker login PROVIDER --via=agent@home-core
```

Keep both harness state directories private. They contain credentials, session
transcripts, history, and artifacts. Include them in an encrypted off-host
backup before relying on session recovery after host disk loss.

## Security and resource boundary

The `agent` user is deliberately absent from `wheel` and `docker`. Do not grant it
access to `/run/secrets`, `/srv/state`, the Docker socket, or production deploy
commands. OMP defaults to prompting before shell execution in this profile;
`--auto-approve` is an explicit per-session choice whose impact is bounded by
the operating-system account.

The user slice is limited to four CPU cores, 16 GiB soft memory pressure, 24 GiB
hard memory usage, and 4096 tasks. Adjust these only after observing contention
against the gateway and automation workloads.

OMP is limited to two concurrent subagents and has isolated task workspaces
enabled. Pi is pinned directly to upstream release 0.85.1 because the older
Nixpkgs 0.75.4 package predates Pi's project-trust security fix.

## Acceptance checks

```bash
id
sudo -n true                 # must fail
docker ps                    # must fail
test ! -r /run/secrets       # must pass
test ! -r /srv/state         # must pass
systemctl status user-1001.slice
```

Then run a harmless task inside tmux, disconnect the MacBook, reconnect from an
approved Tailscale client, and verify that the same process is still live.

## Browser access

Homepage can link to a browser terminal, but it cannot turn an SSH URL into an
embedded terminal. A browser terminal would expose an interactive shell as the
`agent` account and therefore needs its own authenticated HTTPS service. Do not
publish an unauthenticated ttyd/Wetty port merely because it is on the tailnet.
The recommended future design is a loopback-only terminal service behind
Tailscale Serve with identity-aware access, followed by a Homepage link. It is
intentionally not enabled by this deployment.
