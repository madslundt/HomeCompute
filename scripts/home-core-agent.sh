#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: home-core-agent [pi|omp] [repository] [session]
       home-core-agent --list

With no arguments, the remote launcher asks which harness to use.
Repository defaults to HomeCompute and session defaults to HARNESS-REPOSITORY.
EOF
}

if [[ "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if (( $# > 3 )); then
  usage >&2
  exit 2
fi

for argument in "$@"; do
  if [[ ! "$argument" =~ ^(--list|[A-Za-z0-9._-]+)$ ]]; then
    echo "Invalid argument: $argument" >&2
    exit 2
  fi
done

identity_file="${HOME_CORE_AGENT_IDENTITY:-$HOME/.ssh/id_ed25519_ai-services-01}"
target="${HOME_CORE_AGENT_HOST:-agent@home-core}"

# tmux needs clear-screen capabilities. macOS terminals such as Ghostty can
# advertise a TERM entry that is absent on the remote NixOS host, while some
# launch contexts advertise TERM=dumb. xterm-256color is supported by both.
remote_command="TERM=xterm-256color agent-session"
for argument in "$@"; do
  remote_command+=" $argument"
done

exec ssh -t -i "$identity_file" "$target" "$remote_command"
