#!/usr/bin/env bash
# Install only the manager's private workflow API firewall rule on home-core.
set -Eeuo pipefail
unit=homecompute-automation-network.service
dropin="/etc/systemd/system.attached/$unit.d/90-ai-automation-firewall.conf"
baseline=bf3431d53538f446fe799cc6b7651a7d25bef462
chain=HC-AUTOMATION
rule=(-o br-hc-n8n -s 172.28.201.2/32 -d 172.28.201.5/32 -p tcp --dport 8080 -j RETURN)
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
die() { printf '%s\n' "$*" >&2; exit 1; }
[[ $EUID == 0 && $(hostname) == home-core ]] || die "Run as root on home-core."

start_path() {
  local value
  value=$(systemctl show "$unit" -p ExecStart --value)
  [[ $value =~ ^\{\ path=(/nix/store/[a-z0-9]+-unit-script-homecompute-automation-network-start/bin/homecompute-automation-network-start)\ \; ]] ||
    die "Unexpected firewall start command."
  printf '%s\n' "${BASH_REMATCH[1]}"
}

rule_positions() {
  iptables -w -S "$chain" | awk '
    /^-A HC-AUTOMATION / {
      ++row
      if ($0 == "-A HC-AUTOMATION -s 172.28.201.2/32 -d 172.28.201.5/32 -o br-hc-n8n -p tcp -m tcp --dport 8080 -j RETURN") {
        allow = row
        ++matches
      }
      if ($0 ~ /^-A HC-AUTOMATION -d 10[.]0[.]0[.]0\/8 -j REJECT/) deny = row
    }
    END {
      if (!deny || matches > 1 || allow >= deny) exit 1
      print allow + 0, deny
    }'
}

reapply() {
  local positions allow deny
  iptables -w -C DOCKER-USER -i br-hc-n8n -j "$chain" || return 1
  positions=$(rule_positions) || return 1
  read -r allow deny <<< "$positions"
  if [[ $allow == 0 ]]; then
    iptables -w -I "$chain" "$deny" "${rule[@]}" || return 1
  fi
  iptables -w -C "$chain" "${rule[@]}" || return 1
  positions=$(rule_positions) || return 1
  read -r allow deny <<< "$positions"
  (( allow > 0 && allow < deny ))
}

if [[ ${1:-} == reapply && $# == 2 ]]; then
  # A future NixOS deployment may own this policy. An overlay for the old
  # generation must not modify a replacement unit's rules.
  [[ $(start_path) == "$2" ]] || exit 0
  reapply
  exit
fi

[[ $# == 1 ]] || die "Usage: $0 {apply|status|rollback}"
revision=$(git -C "$root" rev-parse HEAD)
[[ $root == "/srv/homecompute/releases/$revision" ]] || die "Use the exact published release checkout."
[[ $(git -C "$root" rev-parse HEAD^) == "$baseline" ]] || die "Expected a hotfix based on the deployed release."
[[ -z $(git -C "$root" status --porcelain --untracked-files=all) ]] || die "Hotfix checkout is dirty."
start=$(start_path)
expected=$(printf '[Service]\nExecStartPost=/run/current-system/sw/bin/bash %s/scripts/setup-ai-automation-firewall.sh reapply %s' "$root" "$start")

if [[ $1 == status ]]; then
  systemctl show "$unit" -p ActiveState -p DropInPaths
  iptables -w -C "$chain" "${rule[@]}"
  rule_positions
  exit
fi

[[ $(cat /var/lib/homecompute/deployed-revision) == "$baseline" ]] || die "The deployed base revision changed."
exec 9>/var/lib/homecompute/deploy.lock
flock -n 9 || die "Another HomeCompute operation owns the deployment lock."
systemctl is-active --quiet "$unit" || die "The automation firewall is inactive."
if [[ -e $dropin || -L $dropin ]]; then
  [[ -f $dropin && ! -L $dropin && $(cat "$dropin") == "$expected" ]] ||
    die "An unexpected drop-in already exists."
fi

case "$1" in
  apply)
    [[ $(git -C "$root" rev-parse origin/codex/hourly-automation-firewall) == "$revision" ]] ||
      die "The selected release is missing from the published hotfix branch."
    for target in "homecompute-automation-n8n-1:172.28.201.2" \
        "homecompute-ai-automation-manager-ai-automation-manager-1:172.28.201.5"; do
      live=$(docker inspect --format '{{.State.Running}} {{(index .NetworkSettings.Networks "homecompute-automation_migration").IPAddress}}' "${target%:*}")
      [[ $live == "true ${target#*:}" ]] || die "Container or migration bridge identity changed."
    done
    positions=$(rule_positions) || die "Unexpected automation firewall order."
    read -r allow deny <<< "$positions"
    if [[ -f $dropin ]]; then
      reapply
      exit
    fi
    [[ $allow == 0 ]] || die "An existing rule has no managed drop-in."
    install -d -m 0755 -- "$(dirname "$dropin")"
    staging=$(mktemp "$(dirname "$dropin")/.ai-automation-firewall.XXXXXX")
    printf '%s\n' "$expected" > "$staging"
    chmod 0644 "$staging"
    mv -- "$staging" "$dropin"
    if ! systemctl daemon-reload ||
       [[ $(systemctl show "$unit" -p DropInPaths --value) != *"$dropin"* ]] ||
       ! reapply; then
      rm -- "$dropin"
      if iptables -w -C "$chain" "${rule[@]}" 2>/dev/null; then iptables -w -D "$chain" "${rule[@]}"; fi
      systemctl daemon-reload
      die "Firewall hotfix failed and was rolled back."
    fi
    printf 'Installed the scoped manager API rule from %s.\n' "$revision"
    ;;
  rollback)
    [[ -f $dropin ]] || die "The managed drop-in is absent."
    rm -- "$dropin"
    systemctl daemon-reload
    if iptables -w -C "$chain" "${rule[@]}" 2>/dev/null; then iptables -w -D "$chain" "${rule[@]}"; fi
    ;;
  *) die "Usage: $0 {apply|status|rollback}" ;;
esac
