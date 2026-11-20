#!/usr/bin/env bash
# Run the same pinned non-interactive stages as OpenBot's Hermes Download.
set -Eeuo pipefail
umask 077
[[ "$(id -u)" == 1000 && "$HOME" == /data ]]
cd /data
exec 9>.homecompute-hermes-install.lock
flock -n 9
[[ ! -x .local/bin/hermes ]] || exit 0

# nightly-labs/openbot v0.73.0, src/main/hermes-installer.ts
readonly commit=b200f5bc60e31588ba2cb60e288029c4a54ba5fa
readonly digest=abf338605dae6dd28d101673ac6aed7995c955745db46d47676bc591a7957209
installer="$(mktemp)"
trap 'rm -f -- "$installer"' EXIT
curl --fail --silent --show-error --proto '=https' --max-time 60 \
  "https://raw.githubusercontent.com/NousResearch/hermes-agent/$commit/scripts/install.sh" -o "$installer"
printf '%s  %s\n' "$digest" "$installer" | sha256sum --check --status

export NO_COLOR=1
for stage in prerequisites repository venv python-deps config products complete; do
  printf 'Installing partner Hermes stage %s\n' "$stage"
  timeout --kill-after=10s 20m bash "$installer" --stage "$stage" --json \
    --commit "$commit" --non-interactive --skip-browser --skip-computer-use </dev/null
done
test -x /data/.local/bin/hermes
