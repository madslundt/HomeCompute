#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

CHAIN=GB10-COMPUTE
INSTALLED_HELPER=/usr/local/libexec/gb10-compute-firewall
CONFIG_FILE=/etc/gb10-ai/firewall.env
UNIT_FILE=/etc/systemd/system/gb10-compute-firewall.service
DOCKER_DROPIN=/etc/systemd/system/docker.service.d/gb10-compute-firewall.conf
declare -a PORTS=()

log() { printf '[compute-firewall] %s\n' "$*"; }
die() { printf '[compute-firewall] ERROR: %s\n' "$*" >&2; exit 1; }
require_root() { [[ ${EUID} -eq 0 ]] || die "Run this command with sudo"; }
require_command() { command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"; }

is_ipv4_address() {
  local octet; local -a octets; local IFS='.'
  read -r -a octets <<<"$1"; ((${#octets[@]} == 4)) || return 1
  for octet in "${octets[@]}"; do
    [[ "$octet" =~ ^[0-9]{1,3}$ ]] && ((10#$octet <= 255)) || return 1
  done
}

validate_policy() {
  is_ipv4_address "$ADDRESS" && [[ "$ADDRESS" != 0.0.0.0 ]] || die "Listener must be a non-wildcard IPv4 address"
  local source="${SOURCE_CIDR%/*}" prefix="${SOURCE_CIDR##*/}" port port_number previous=-1
  [[ "$SOURCE_CIDR" == */* ]] || prefix=32
  if ! is_ipv4_address "$source" || [[ ! "$prefix" =~ ^[0-9]{1,2}$ ]] || ((10#$prefix != 32)); then die "Source must be one dedicated IPv4 /32"; fi
  SOURCE_CIDR="$source/32"
  [[ -n "$PORTS_CSV" && "$PORTS_CSV" != *[[:space:]]* &&
    "$PORTS_CSV" =~ ^[0-9]+(,[0-9]+)*$ ]] ||
    die "Ports must be a non-empty comma-separated list without whitespace"
  local IFS=','
  read -r -a PORTS <<<"$PORTS_CSV"
  for port in "${PORTS[@]}"; do
    [[ "$port" =~ ^[0-9]+$ ]] || die "Every port must be numeric"
    port_number=$((10#$port))
    ((port_number >= 1024 && port_number <= 65535)) || die "Every port must be 1024 through 65535"
    [[ "$port" == "$port_number" ]] || die "Ports must use canonical decimal notation"
    ((port_number > previous)) || die "Ports must be sorted and unique"
    previous=$port_number
  done
}

load_config() {
  local path="$1" key value seen_address=false seen_source=false seen_ports=false mode parent
  [[ -f "$path" && ! -L "$path" ]] || die "Firewall config must be a regular non-symlink: $path"
  [[ "$(stat -c '%u' "$path")" == 0 ]] || die "Firewall config must be owned by root"
  mode="$(stat -c '%a' "$path")"; (( (8#$mode & 8#022) == 0 )) || die "Firewall config must not be group/world-writable"
  parent="$(dirname -- "$path")"
  [[ -d "$parent" && ! -L "$parent" && "$(stat -c '%u' "$parent")" == 0 ]] ||
    die "Firewall config parent must be a root-owned non-symlink directory"
  while IFS='=' read -r key value; do
    case "$key" in
      GB10_BIND_ADDRESS) [[ "$seen_address" == false ]] || die "Duplicate GB10_BIND_ADDRESS"; ADDRESS="$value"; seen_address=true ;;
      GATEWAY_CIDR) [[ "$seen_source" == false ]] || die "Duplicate GATEWAY_CIDR"; SOURCE_CIDR="$value"; seen_source=true ;;
      COMPUTE_HOST_PORTS) [[ "$seen_ports" == false ]] || die "Duplicate COMPUTE_HOST_PORTS"; PORTS_CSV="$value"; seen_ports=true ;;
      '') ;;
      *) die "Unexpected firewall config key: $key" ;;
    esac
  done <"$path"
  [[ "$seen_address:$seen_source:$seen_ports" == true:true:true ]] || die "Firewall config is incomplete"
}

policy_args() {
  if [[ "${1:-}" == --config ]]; then
    (($# == 2)) || die "--config requires exactly one path"
    load_config "$2"
  else
    (($# == 3)) || die "Expected ADDRESS SOURCE_CIDR PORTS_CSV"
    ADDRESS="$1"; SOURCE_CIDR="$2"; PORTS_CSV="$3"
  fi
  validate_policy
}

apply_policy() {
  require_root; require_command iptables
  local -a rules docker_user_rules
  local index port peer_address="${SOURCE_CIDR%/*}"
  iptables -w -N DOCKER-USER 2>/dev/null || true
  iptables -w -N "$CHAIN" 2>/dev/null || true
  # Install a temporary deny guard before replacing an existing policy, so
  # reconciliation can transiently deny other forwarded traffic but can never
  # expose either the old or new listener/link tuple.
  iptables -w -I "$CHAIN" 1 -j REJECT --reject-with icmp-port-unreachable
  while true; do
    mapfile -t rules < <(iptables -w -S "$CHAIN")
    ((${#rules[@]} > 2)) || break
    iptables -w -D "$CHAIN" 2
  done
  for port in "${PORTS[@]}"; do
    iptables -w -A "$CHAIN" -p tcp -m conntrack --ctorigsrc "$SOURCE_CIDR" --ctorigdst "$ADDRESS" --ctorigdstport "$port" -j ACCEPT
    iptables -w -A "$CHAIN" -p tcp -m conntrack --ctorigdst "$ADDRESS" --ctorigdstport "$port" -j REJECT --reject-with tcp-reset
  done
  iptables -w -A "$CHAIN" -m conntrack --ctorigsrc "$peer_address/32" -j REJECT --reject-with icmp-port-unreachable
  iptables -w -A "$CHAIN" -m conntrack --ctorigdst "$peer_address/32" -j REJECT --reject-with icmp-port-unreachable
  iptables -w -A "$CHAIN" -j RETURN
  iptables -w -D "$CHAIN" 1
  iptables -w -I DOCKER-USER 1 -j "$CHAIN"
  mapfile -t docker_user_rules < <(iptables -w -S DOCKER-USER)
  for ((index=${#docker_user_rules[@]}-1; index>1; index--)); do
    [[ "${docker_user_rules[$index]}" != "-A DOCKER-USER -j $CHAIN" ]] ||
      iptables -w -D DOCKER-USER "$index"
  done
}

verify_policy() {
  require_root; require_command iptables
  local -a chain_rules docker_user_rules expected_rules
  local jump_count=0 peer_address="${SOURCE_CIDR%/*}" rule port index
  for port in "${PORTS[@]}"; do
    expected_rules+=("-A $CHAIN -p tcp -m conntrack --ctorigsrc $SOURCE_CIDR --ctorigdst $ADDRESS --ctorigdstport $port -j ACCEPT")
    expected_rules+=("-A $CHAIN -p tcp -m conntrack --ctorigdst $ADDRESS --ctorigdstport $port -j REJECT --reject-with tcp-reset")
  done
  expected_rules+=(
    "-A $CHAIN -m conntrack --ctorigsrc $peer_address/32 -j REJECT --reject-with icmp-port-unreachable"
    "-A $CHAIN -m conntrack --ctorigdst $peer_address/32 -j REJECT --reject-with icmp-port-unreachable"
    "-A $CHAIN -j RETURN"
  )
  mapfile -t chain_rules < <(iptables -w -S "$CHAIN" 2>/dev/null) || die "Missing $CHAIN chain"
  ((${#chain_rules[@]} == ${#expected_rules[@]} + 1)) ||
    die "$CHAIN must contain exactly ${#expected_rules[@]} repository-owned rules"
  for ((index=0; index<${#expected_rules[@]}; index++)); do
    [[ "${chain_rules[$((index+1))]}" == "${expected_rules[$index]}" ]] ||
      die "$CHAIN rules do not exactly match the ordered repository policy"
  done
  mapfile -t docker_user_rules < <(iptables -w -S DOCKER-USER)
  ((${#docker_user_rules[@]} >= 2)) && [[ "${docker_user_rules[1]}" == "-A DOCKER-USER -j $CHAIN" ]] ||
    die "$CHAIN jump is not first in DOCKER-USER"
  for rule in "${docker_user_rules[@]}"; do
    [[ "$rule" != "-A DOCKER-USER -j $CHAIN" ]] || jump_count=$((jump_count+1))
  done
  ((jump_count == 1)) || die "DOCKER-USER must contain exactly one $CHAIN jump"
  log "Verified ${#PORTS[@]} exact listener allow/reject pair(s) and isolated ${peer_address}/32 forwarding policy"
}

safe_install_target() {
  local path="$1" parent
  parent="$(dirname -- "$path")"
  [[ ! -e "$path" && ! -L "$path" ]] || [[ -f "$path" && ! -L "$path" ]] || die "Install target is not a regular file: $path"
  [[ ! -e "$parent" && ! -L "$parent" ]] || [[ -d "$parent" && ! -L "$parent" ]] || die "Install parent is not a directory: $parent"
}

install_policy() {
  require_root
  for command in install mktemp systemctl; do require_command "$command"; done
  safe_install_target "$INSTALLED_HELPER"; safe_install_target "$CONFIG_FILE"
  safe_install_target "$UNIT_FILE"; safe_install_target "$DOCKER_DROPIN"
  install -d -m 0755 -o root -g root "$(dirname -- "$INSTALLED_HELPER")"
  install -d -m 0750 -o root -g root "$(dirname -- "$CONFIG_FILE")"
  install -d -m 0755 -o root -g root "$(dirname -- "$DOCKER_DROPIN")"
  install -m 0755 -o root -g root "$0" "$INSTALLED_HELPER"
  local config_tmp unit_tmp dropin_tmp
  config_tmp="$(mktemp "${CONFIG_FILE}.tmp.XXXXXX")"
  unit_tmp="$(mktemp "${UNIT_FILE}.tmp.XXXXXX")"
  dropin_tmp="$(mktemp "${DOCKER_DROPIN}.tmp.XXXXXX")"
  printf 'GB10_BIND_ADDRESS=%s\nGATEWAY_CIDR=%s\nCOMPUTE_HOST_PORTS=%s\n' "$ADDRESS" "$SOURCE_CIDR" "$PORTS_CSV" >"$config_tmp"
  cat >"$unit_tmp" <<'UNIT'
[Unit]
Description=HomeCompute compute-node ingress policy
Before=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/libexec/gb10-compute-firewall apply --config /etc/gb10-ai/firewall.env
ExecStartPost=/usr/local/libexec/gb10-compute-firewall verify --config /etc/gb10-ai/firewall.env
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
UNIT
  cat >"$dropin_tmp" <<'DROPIN'
[Unit]
Requires=gb10-compute-firewall.service
After=gb10-compute-firewall.service

[Service]
ExecStartPost=/usr/local/libexec/gb10-compute-firewall apply --config /etc/gb10-ai/firewall.env
ExecStartPost=/usr/local/libexec/gb10-compute-firewall verify --config /etc/gb10-ai/firewall.env
DROPIN
  chown root:root "$config_tmp" "$unit_tmp" "$dropin_tmp"
  chmod 0640 "$config_tmp"; chmod 0644 "$unit_tmp" "$dropin_tmp"
  mv -fT "$config_tmp" "$CONFIG_FILE"
  mv -fT "$unit_tmp" "$UNIT_FILE"
  mv -fT "$dropin_tmp" "$DOCKER_DROPIN"
  systemctl daemon-reload
  systemctl enable --now gb10-compute-firewall.service >/dev/null
  apply_policy; verify_policy
  log "Installed persistent policy before and after Docker startup on every reboot"
}

COMMAND="${1:-}"; (($# == 0)) || shift
case "$COMMAND" in
  apply) policy_args "$@"; apply_policy ;;
  verify) policy_args "$@"; verify_policy ;;
  install) policy_args "$@"; install_policy ;;
  *) die "Usage: $0 {install|apply|verify} ADDRESS SOURCE_CIDR PORTS_CSV | {apply|verify} --config FILE" ;;
esac
