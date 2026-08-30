#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ENV_TEMPLATE="$REPO_ROOT/config/services-node.env.example"
VENDOR_TEMPLATE="$REPO_ROOT/deploy/services-node/cloud-init-vendor.yaml"

COMMAND="${1:-help}"
if (($# > 0)); then
  shift
fi

ENV_FILE="${SERVICES_NODE_ENV_FILE:-/etc/ai-platform/services-node.env}"

usage() {
  cat <<'USAGE'
Usage: setup-services-node.sh COMMAND [OPTIONS]

Commands:
  init             Copy the operator configuration template to /etc
  validate         Validate configuration without changing the host
  preflight        Inspect Proxmox, CPU, RAM, storage, bridges, and VM IDs
  host-packages    Update package indexes and install the minimal host tools
  create-template Download the pinned Debian image and create a VM template
  provision        Clone and configure the gateway, automation, and toolbox VMs
  start            Start the three provisioned VMs in dependency order
  status           Show relevant host, storage, bridge, and VM state
  help             Show this help

Options:
  --env FILE       Configuration file (default: /etc/ai-platform/services-node.env)

Safe first run on a freshly installed Proxmox VE host:
  sudo ./scripts/setup-services-node.sh init
  sudoedit /etc/ai-platform/services-node.env
  sudo ./scripts/setup-services-node.sh validate
  sudo ./scripts/setup-services-node.sh preflight
  sudo ./scripts/setup-services-node.sh host-packages
  sudo ./scripts/setup-services-node.sh create-template
  sudo ./scripts/setup-services-node.sh provision
  sudo ./scripts/setup-services-node.sh status

The script never installs Proxmox, edits /etc/network/interfaces, creates a
cluster, configures a backup target, disables a subscription repository, or
deletes an existing VM. Those actions require operator-specific decisions.
USAGE
}

log() {
  printf '[services-node] %s\n' "$*"
}

warn() {
  printf '[services-node] WARNING: %s\n' "$*" >&2
}

die() {
  printf '[services-node] ERROR: %s\n' "$*" >&2
  exit 1
}

require_root() {
  [[ ${EUID} -eq 0 ]] || die "Run this command as root (or with sudo) on the Proxmox host"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

parse_options() {
  while (($# > 0)); do
    case "$1" in
      --env)
        (($# >= 2)) || die "--env requires a file"
        ENV_FILE="$2"
        shift 2
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        die "Unknown option: $1"
        ;;
    esac
  done
}

load_env() {
  [[ -f "$ENV_FILE" ]] || die "Configuration not found: $ENV_FILE (run init first)"
  [[ ! -L "$ENV_FILE" ]] || die "Configuration must not be a symbolic link: $ENV_FILE"
  # This is a root-owned operator file. Never source configuration received
  # from an untrusted party because shell environment files are executable.
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
}

is_placeholder() {
  [[ -z "$1" || "$1" == *REPLACE_WITH* || "$1" == *CHANGEME* || "$1" == *TODO* ]]
}

require_value() {
  local name="$1"
  local value="${!name:-}"
  [[ -n "$value" ]] || die "Missing required setting: $name"
  if is_placeholder "$value"; then
    die "Replace placeholder setting: $name"
  fi
}

is_uint() {
  [[ "$1" =~ ^[0-9]+$ ]]
}

is_ipv4() {
  local address="$1"
  local octet
  local -a octets
  local IFS='.'

  read -r -a octets <<<"$address"
  ((${#octets[@]} == 4)) || return 1
  for octet in "${octets[@]}"; do
    [[ "$octet" =~ ^[0-9]{1,3}$ ]] || return 1
    ((10#$octet >= 0 && 10#$octet <= 255)) || return 1
  done
}

is_ipv4_cidr() {
  local value="$1"
  local address="${value%/*}"
  local prefix="${value##*/}"

  [[ "$value" == */* ]] || return 1
  is_ipv4 "$address" || return 1
  [[ "$prefix" =~ ^[0-9]{1,2}$ ]] || return 1
  ((10#$prefix >= 0 && 10#$prefix <= 32))
}

validate_name() {
  local setting="$1"
  local value="${!setting}"
  [[ "$value" =~ ^[a-z][a-z0-9-]{0,31}$ ]] ||
    die "$setting must be a lowercase DNS-style name: $value"
}

validate_vm_shape() {
  local prefix="$1"
  local id_var="${prefix}_VM_ID"
  local cores_var="${prefix}_VM_CORES"
  local memory_var="${prefix}_VM_MEMORY_MB"
  local balloon_var="${prefix}_VM_BALLOON_MB"
  local disk_var="${prefix}_VM_DISK"
  local id="${!id_var}"
  local cores="${!cores_var}"
  local memory="${!memory_var}"
  local balloon="${!balloon_var}"
  local disk="${!disk_var}"

  if ! is_uint "$id" || ! ((10#$id >= 100 && 10#$id <= 999999999)); then
    die "$id_var is invalid"
  fi
  if ! is_uint "$cores" || ! ((10#$cores >= 1 && 10#$cores <= 32)); then
    die "$cores_var is invalid"
  fi
  if ! is_uint "$memory" || ! ((10#$memory >= 2048)); then
    die "$memory_var must be at least 2048"
  fi
  if ! is_uint "$balloon" || ! ((10#$balloon >= 2048 && 10#$balloon <= 10#$memory)); then
    die "$balloon_var must be between 2048 and $memory"
  fi
  [[ "$disk" =~ ^[1-9][0-9]*G$ ]] || die "$disk_var must look like 120G"
}

validate_config() {
  local name
  local -a required=(
    PVE_MAJOR_VERSION PVE_HOSTNAME TIMEZONE VM_STORAGE SNIPPET_STORAGE LAN_BRIDGE COMPUTE_BRIDGE
    COMPUTE_PRIVATE_CIDR COMPUTE_EXPECTED_IP CLOUD_IMAGE_URL CLOUD_IMAGE_SHA512
    CLOUD_IMAGE_CACHE TEMPLATE_ID TEMPLATE_NAME VM_ADMIN_USER SSH_PUBLIC_KEY_FILE
    DNS_SERVER DNS_SEARCH_DOMAIN LAN_GATEWAY GATEWAY_VM_ID GATEWAY_VM_NAME GATEWAY_VM_IP
    GATEWAY_VM_COMPUTE_IP GATEWAY_VM_CORES GATEWAY_VM_MEMORY_MB GATEWAY_VM_BALLOON_MB
    GATEWAY_VM_DISK AUTOMATION_VM_ID AUTOMATION_VM_NAME AUTOMATION_VM_IP
    AUTOMATION_VM_CORES AUTOMATION_VM_MEMORY_MB AUTOMATION_VM_BALLOON_MB
    AUTOMATION_VM_DISK TOOLBOX_VM_ID TOOLBOX_VM_NAME TOOLBOX_VM_IP TOOLBOX_VM_CORES
    TOOLBOX_GATEWAY TOOLBOX_DNS_SERVER TOOLBOX_VM_MEMORY_MB TOOLBOX_VM_BALLOON_MB
    TOOLBOX_VM_DISK TOOLBOX_VLAN_TAG START_VMS
  )

  load_env
  for name in "${required[@]}"; do
    require_value "$name"
  done

  is_uint "$PVE_MAJOR_VERSION" || die "PVE_MAJOR_VERSION must be numeric"
  validate_name PVE_HOSTNAME
  [[ "$TIMEZONE" =~ ^[A-Za-z_+-]+/[A-Za-z0-9_+/-]+$ ]] || die "TIMEZONE is malformed"
  [[ "$VM_STORAGE" =~ ^[A-Za-z0-9_-]+$ ]] || die "VM_STORAGE is malformed"
  [[ "$SNIPPET_STORAGE" =~ ^[A-Za-z0-9_-]+$ ]] || die "SNIPPET_STORAGE is malformed"
  [[ "$LAN_BRIDGE" =~ ^[A-Za-z0-9_.:-]+$ ]] || die "LAN_BRIDGE is malformed"
  [[ "$COMPUTE_BRIDGE" =~ ^[A-Za-z0-9_.:-]+$ ]] || die "COMPUTE_BRIDGE is malformed"
  is_ipv4_cidr "$COMPUTE_PRIVATE_CIDR" || die "COMPUTE_PRIVATE_CIDR is invalid"
  is_ipv4 "$COMPUTE_EXPECTED_IP" || die "COMPUTE_EXPECTED_IP is invalid"
  [[ "$CLOUD_IMAGE_URL" =~ ^https://[^[:space:]]+$ ]] || die "CLOUD_IMAGE_URL must use HTTPS"
  [[ "$CLOUD_IMAGE_SHA512" =~ ^[a-fA-F0-9]{128}$ ]] || die "CLOUD_IMAGE_SHA512 must be 128 hex characters"
  [[ "$CLOUD_IMAGE_CACHE" == /var/lib/vz/template/iso/* ]] ||
    die "CLOUD_IMAGE_CACHE must stay under /var/lib/vz/template/iso"
  if ! is_uint "$TEMPLATE_ID" || ! ((10#$TEMPLATE_ID >= 100)); then
    die "TEMPLATE_ID is invalid"
  fi
  validate_name TEMPLATE_NAME
  validate_name VM_ADMIN_USER
  [[ "$SSH_PUBLIC_KEY_FILE" == /* ]] || die "SSH_PUBLIC_KEY_FILE must be absolute"
  is_ipv4 "$DNS_SERVER" || die "DNS_SERVER is invalid"
  [[ "$DNS_SEARCH_DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || die "DNS_SEARCH_DOMAIN is malformed"

  validate_name GATEWAY_VM_NAME
  validate_name AUTOMATION_VM_NAME
  validate_name TOOLBOX_VM_NAME
  is_ipv4_cidr "$GATEWAY_VM_IP" || die "GATEWAY_VM_IP is invalid"
  is_ipv4_cidr "$GATEWAY_VM_COMPUTE_IP" || die "GATEWAY_VM_COMPUTE_IP is invalid"
  is_ipv4_cidr "$AUTOMATION_VM_IP" || die "AUTOMATION_VM_IP is invalid"
  is_ipv4_cidr "$TOOLBOX_VM_IP" || die "TOOLBOX_VM_IP is invalid"
  is_ipv4 "$LAN_GATEWAY" || die "LAN_GATEWAY is invalid"
  is_ipv4 "$TOOLBOX_GATEWAY" || die "TOOLBOX_GATEWAY is invalid"
  is_ipv4 "$TOOLBOX_DNS_SERVER" || die "TOOLBOX_DNS_SERVER is invalid"

  validate_vm_shape GATEWAY
  validate_vm_shape AUTOMATION
  validate_vm_shape TOOLBOX

  [[ "$GATEWAY_VM_ID" != "$AUTOMATION_VM_ID" && "$GATEWAY_VM_ID" != "$TOOLBOX_VM_ID" &&
     "$AUTOMATION_VM_ID" != "$TOOLBOX_VM_ID" && "$TEMPLATE_ID" != "$GATEWAY_VM_ID" &&
     "$TEMPLATE_ID" != "$AUTOMATION_VM_ID" && "$TEMPLATE_ID" != "$TOOLBOX_VM_ID" ]] ||
    die "Template and VM IDs must be unique"
  if ! is_uint "$TOOLBOX_VLAN_TAG" ||
     ! ((10#$TOOLBOX_VLAN_TAG >= 0 && 10#$TOOLBOX_VLAN_TAG <= 4094)); then
    die "TOOLBOX_VLAN_TAG must be 0 (untagged) or 1-4094"
  fi
  [[ "$START_VMS" == "true" || "$START_VMS" == "false" ]] || die "START_VMS must be true or false"

  log "Configuration is valid: $ENV_FILE"
}

require_pve() {
  local version

  [[ -d /etc/pve ]] || die "This command must run on a Proxmox VE host"
  require_command pveversion
  version="$(pveversion | sed -nE 's#^pve-manager/([0-9]+)\..*#\1#p')"
  [[ -n "$version" ]] || die "Could not determine the Proxmox VE major version"
  [[ "$version" == "$PVE_MAJOR_VERSION" ]] ||
    die "Expected Proxmox VE $PVE_MAJOR_VERSION.x, found $version.x; review before proceeding"
  [[ "$(hostname -s)" == "$PVE_HOSTNAME" ]] ||
    die "Expected host name $PVE_HOSTNAME, found $(hostname -s)"
}

storage_has_snippets() {
  pvesm config "$SNIPPET_STORAGE" 2>/dev/null |
    awk '$1 == "content" {print $2}' |
    tr ',' '\n' |
    grep -Fxq snippets
}

check_public_key() {
  [[ -f "$SSH_PUBLIC_KEY_FILE" && ! -L "$SSH_PUBLIC_KEY_FILE" ]] ||
    die "SSH public key file is missing or a symlink: $SSH_PUBLIC_KEY_FILE"
  [[ "$(wc -l <"$SSH_PUBLIC_KEY_FILE" | tr -d ' ')" == "1" ]] ||
    die "SSH_PUBLIC_KEY_FILE must contain exactly one public key"
  grep -Eq '^(ssh-(ed25519|rsa)|ecdsa-sha2-nistp(256|384|521)) [A-Za-z0-9+/=]+' "$SSH_PUBLIC_KEY_FILE" ||
    die "SSH_PUBLIC_KEY_FILE does not look like an OpenSSH public key"
}

run_preflight() {
  local memory_kib
  local memory_gib
  local id

  validate_config
  require_pve
  require_command qm
  require_command pvesm
  require_command ip
  require_command sha512sum
  require_command curl

  grep -Eq '\b(vmx|svm)\b' /proc/cpuinfo || die "CPU virtualization is unavailable; enable VT-x in firmware"
  memory_kib="$(awk '/^MemTotal:/ {print $2}' /proc/meminfo)"
  memory_gib="$((memory_kib / 1024 / 1024))"
  ((memory_gib >= 44)) || die "Expected at least 44 GiB usable RAM; found ${memory_gib} GiB"

  pvesm status --storage "$VM_STORAGE" >/dev/null 2>&1 || die "VM storage not found: $VM_STORAGE"
  pvesm status --storage "$SNIPPET_STORAGE" >/dev/null 2>&1 || die "Snippet storage not found: $SNIPPET_STORAGE"
  storage_has_snippets || die "Enable the Snippets content type on storage $SNIPPET_STORAGE"
  [[ -d "/sys/class/net/$LAN_BRIDGE" ]] || die "LAN bridge does not exist: $LAN_BRIDGE"
  [[ -d "/sys/class/net/$COMPUTE_BRIDGE" ]] || die "Private compute bridge does not exist: $COMPUTE_BRIDGE"
  check_public_key

  for id in "$GATEWAY_VM_ID" "$AUTOMATION_VM_ID" "$TOOLBOX_VM_ID"; do
    if qm status "$id" >/dev/null 2>&1; then
      warn "VM ID $id already exists; provision will refuse to overwrite it"
    fi
  done

  log "Proxmox $(pveversion | head -n1)"
  log "Usable memory: ${memory_gib} GiB"
  log "VM storage: $VM_STORAGE; snippet storage: $SNIPPET_STORAGE"
  log "LAN bridge: $LAN_BRIDGE; private compute bridge: $COMPUTE_BRIDGE"
  ip -brief link show "$LAN_BRIDGE" "$COMPUTE_BRIDGE"
  pvesm status --storage "$VM_STORAGE"
  log "Preflight passed"
}

init_config() {
  require_root
  [[ -f "$ENV_TEMPLATE" ]] || die "Environment template not found: $ENV_TEMPLATE"
  [[ ! -e "$ENV_FILE" ]] || die "Refusing to overwrite existing configuration: $ENV_FILE"
  install -d -m 0750 -o root -g root "$(dirname -- "$ENV_FILE")"
  install -m 0600 -o root -g root "$ENV_TEMPLATE" "$ENV_FILE"
  log "Created $ENV_FILE; edit every REPLACE_WITH value before validation"
}

install_host_packages() {
  validate_config
  require_pve
  require_root
  require_command apt-get
  require_command timedatectl

  timedatectl set-timezone "$TIMEZONE"
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ca-certificates curl ethtool intel-microcode jq lm-sensors smartmontools
  systemctl enable --now smartmontools.service 2>/dev/null ||
    systemctl enable --now smartd.service 2>/dev/null ||
    warn "Could not enable a SMART monitoring service; inspect the installed unit names"
  log "Host packages installed. Apply Proxmox upgrades only in a planned maintenance window"
}

install_vendor_snippet() {
  local destination

  [[ -f "$VENDOR_TEMPLATE" ]] || die "Cloud-init vendor template not found: $VENDOR_TEMPLATE"
  destination="$(pvesm path "${SNIPPET_STORAGE}:snippets/services-node-vendor.yaml")"
  install -d -m 0755 "$(dirname -- "$destination")"
  install -m 0644 -o root -g root "$VENDOR_TEMPLATE" "$destination"
  printf '%s\n' "$destination"
}

download_cloud_image() {
  local actual

  install -d -m 0755 "$(dirname -- "$CLOUD_IMAGE_CACHE")"
  if [[ -f "$CLOUD_IMAGE_CACHE" ]]; then
    actual="$(sha512sum "$CLOUD_IMAGE_CACHE" | awk '{print $1}')"
    if [[ "$actual" == "${CLOUD_IMAGE_SHA512,,}" ]]; then
      log "Using cached, checksum-verified cloud image"
      return
    fi
    die "Cached image checksum differs; move it aside and rerun: $CLOUD_IMAGE_CACHE"
  fi

  log "Downloading pinned Debian cloud image"
  curl --fail --location --proto '=https' --tlsv1.2 --retry 3 \
    --output "${CLOUD_IMAGE_CACHE}.partial" "$CLOUD_IMAGE_URL"
  printf '%s  %s\n' "$CLOUD_IMAGE_SHA512" "${CLOUD_IMAGE_CACHE}.partial" | sha512sum --check --status || {
    rm -f -- "${CLOUD_IMAGE_CACHE}.partial"
    die "Cloud image SHA-512 verification failed"
  }
  mv -- "${CLOUD_IMAGE_CACHE}.partial" "$CLOUD_IMAGE_CACHE"
  chmod 0644 "$CLOUD_IMAGE_CACHE"
}

create_template() {
  local snippet_path

  validate_config
  require_pve
  require_root
  require_command qm
  require_command pvesm
  require_command curl
  require_command sha512sum
  storage_has_snippets || die "Enable the Snippets content type on storage $SNIPPET_STORAGE"
  check_public_key

  if qm status "$TEMPLATE_ID" >/dev/null 2>&1; then
    die "Template/VM ID $TEMPLATE_ID already exists; refusing to overwrite it"
  fi

  download_cloud_image
  snippet_path="$(install_vendor_snippet)"
  log "Installed cloud-init vendor data at $snippet_path"

  qm create "$TEMPLATE_ID" \
    --name "$TEMPLATE_NAME" \
    --description "Checksum-pinned Debian 13 base for ai-services-01" \
    --ostype l26 \
    --machine q35 \
    --cpu host \
    --cores 2 \
    --memory 2048 \
    --agent enabled=1,fstrim_cloned_disks=1 \
    --scsihw virtio-scsi-single \
    --net0 "virtio,bridge=$LAN_BRIDGE,firewall=1" \
    --serial0 socket \
    --vga serial0
  qm set "$TEMPLATE_ID" --scsi0 "${VM_STORAGE}:0,import-from=${CLOUD_IMAGE_CACHE},discard=on,iothread=1,ssd=1"
  qm set "$TEMPLATE_ID" --ide2 "${VM_STORAGE}:cloudinit"
  qm set "$TEMPLATE_ID" --boot order=scsi0
  qm set "$TEMPLATE_ID" --ciuser "$VM_ADMIN_USER" --sshkeys "$SSH_PUBLIC_KEY_FILE"
  qm set "$TEMPLATE_ID" --cicustom "vendor=${SNIPPET_STORAGE}:snippets/services-node-vendor.yaml"
  qm template "$TEMPLATE_ID"
  log "Created template $TEMPLATE_ID ($TEMPLATE_NAME)"
}

assert_provision_targets_absent() {
  local id

  qm status "$TEMPLATE_ID" >/dev/null 2>&1 || die "Template $TEMPLATE_ID does not exist; run create-template"
  for id in "$GATEWAY_VM_ID" "$AUTOMATION_VM_ID" "$TOOLBOX_VM_ID"; do
    qm status "$id" >/dev/null 2>&1 && die "VM ID $id already exists; refusing a partial overwrite"
  done
  return 0
}

configure_common_vm() {
  local id="$1"
  local name="$2"
  local ip_cidr="$3"
  local gateway="$4"
  local nameserver="$5"
  local cores="$6"
  local memory="$7"
  local balloon="$8"
  local disk="$9"
  local startup_order="${10}"
  local vlan_tag="${11}"
  local net0="virtio,bridge=$LAN_BRIDGE,firewall=1"

  if ((10#$vlan_tag > 0)); then
    net0+=",tag=$vlan_tag"
  fi

  qm clone "$TEMPLATE_ID" "$id" --name "$name" --full 1 --storage "$VM_STORAGE"
  qm set "$id" \
    --cpu host \
    --cores "$cores" \
    --memory "$memory" \
    --balloon "$balloon" \
    --agent enabled=1,fstrim_cloned_disks=1 \
    --onboot 1 \
    --startup "order=${startup_order},up=30,down=60" \
    --net0 "$net0" \
    --ipconfig0 "ip=${ip_cidr},gw=${gateway}" \
    --ciuser "$VM_ADMIN_USER" \
    --sshkeys "$SSH_PUBLIC_KEY_FILE" \
    --nameserver "$nameserver" \
    --searchdomain "$DNS_SEARCH_DOMAIN" \
    --tags "ai-services;debian13"
  qm disk resize "$id" scsi0 "$disk"
}

provision_vms() {
  validate_config
  require_pve
  require_root
  require_command qm
  [[ -d "/sys/class/net/$LAN_BRIDGE" ]] || die "LAN bridge does not exist: $LAN_BRIDGE"
  [[ -d "/sys/class/net/$COMPUTE_BRIDGE" ]] || die "Compute bridge does not exist: $COMPUTE_BRIDGE"
  check_public_key
  assert_provision_targets_absent

  configure_common_vm "$GATEWAY_VM_ID" "$GATEWAY_VM_NAME" "$GATEWAY_VM_IP" "$LAN_GATEWAY" "$DNS_SERVER" \
    "$GATEWAY_VM_CORES" "$GATEWAY_VM_MEMORY_MB" "$GATEWAY_VM_BALLOON_MB" \
    "$GATEWAY_VM_DISK" 1 0
  qm set "$GATEWAY_VM_ID" \
    --net1 "virtio,bridge=$COMPUTE_BRIDGE,firewall=1" \
    --ipconfig1 "ip=${GATEWAY_VM_COMPUTE_IP}" \
    --description "AI API gateway; only guest with direct ai-compute-01 network access" \
    --tags "ai-services;debian13;gateway;compute-access"

  configure_common_vm "$AUTOMATION_VM_ID" "$AUTOMATION_VM_NAME" "$AUTOMATION_VM_IP" "$LAN_GATEWAY" "$DNS_SERVER" \
    "$AUTOMATION_VM_CORES" "$AUTOMATION_VM_MEMORY_MB" "$AUTOMATION_VM_BALLOON_MB" \
    "$AUTOMATION_VM_DISK" 2 0
  qm set "$AUTOMATION_VM_ID" \
    --description "Durable n8n, MCP, browser-worker, and agent application host" \
    --tags "ai-services;debian13;automation;durable"

  configure_common_vm "$TOOLBOX_VM_ID" "$TOOLBOX_VM_NAME" "$TOOLBOX_VM_IP" "$TOOLBOX_GATEWAY" "$TOOLBOX_DNS_SERVER" \
    "$TOOLBOX_VM_CORES" "$TOOLBOX_VM_MEMORY_MB" "$TOOLBOX_VM_BALLOON_MB" \
    "$TOOLBOX_VM_DISK" 3 "$TOOLBOX_VLAN_TAG"
  qm set "$TOOLBOX_VM_ID" \
    --description "Restricted development, CI, framework, and experimental tool runner" \
    --tags "ai-services;debian13;toolbox;restricted"

  if [[ "$START_VMS" == "true" ]]; then
    start_vms
  else
    log 'VMs created but not started. Inspect qm config ID, then run the start command'
  fi
}

start_vms() {
  local id

  load_env
  require_pve
  require_root
  for id in "$GATEWAY_VM_ID" "$AUTOMATION_VM_ID" "$TOOLBOX_VM_ID"; do
    qm status "$id" >/dev/null 2>&1 || die "VM $id does not exist"
    if [[ "$(qm status "$id" | awk '{print $2}')" == "running" ]]; then
      log "VM $id is already running"
    else
      qm start "$id"
      log "Started VM $id"
    fi
  done
}

show_status() {
  load_env
  require_pve
  require_command qm
  require_command pvesm
  require_command ip

  pveversion
  printf '\nRelevant VMs\n'
  qm list | awk -v template="$TEMPLATE_ID" -v gateway="$GATEWAY_VM_ID" \
    -v automation="$AUTOMATION_VM_ID" -v toolbox="$TOOLBOX_VM_ID" \
    'NR == 1 || $1 == template || $1 == gateway || $1 == automation || $1 == toolbox'
  printf '\nStorage\n'
  pvesm status --storage "$VM_STORAGE"
  pvesm status --storage "$SNIPPET_STORAGE"
  printf '\nBridges\n'
  ip -brief address show "$LAN_BRIDGE" "$COMPUTE_BRIDGE" 2>/dev/null || true
}

parse_options "$@"

case "$COMMAND" in
  init)
    init_config
    ;;
  validate)
    validate_config
    ;;
  preflight)
    require_root
    run_preflight
    ;;
  host-packages)
    install_host_packages
    ;;
  create-template)
    create_template
    ;;
  provision)
    provision_vms
    ;;
  start)
    start_vms
    ;;
  status)
    show_status
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    usage >&2
    die "Unknown command: $COMMAND"
    ;;
esac
