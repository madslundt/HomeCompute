#!/usr/bin/env bash
# Manage only the synthetic Hermes owner canary inside the Ubuntu agents guest.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
RELEASE_MANIFEST="$REPO_ROOT/config/hermes-release.json"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib/config.sh"

readonly EXPECTED_NEMOCLAW_COMMIT=26922313bba96184e65c3663b351683ebae9504d
readonly EXPECTED_NEMOCLAW_VERSION=0.0.129
readonly EXPECTED_INSTALLER_SHA256=738cb07356dc5638ca91351ef78c1e0af7cb689a4bdf579726408306e282e72c
readonly EXPECTED_HERMES_VERSION=0.21.3
readonly EXPECTED_OPENSHELL_VERSION=0.0.116
OS_RELEASE_FILE=/etc/os-release

CONFIG_KEYS=(
  HERMES_SANDBOX_NAME
  HERMES_ENDPOINT_URL
  HERMES_MODEL
  HERMES_TRUSTED_PRIVATE_HOSTS
  HERMES_CA_BUNDLE
  HERMES_LITELLM_API_KEY_FILE
  HERMES_EVIDENCE_DIR
  HERMES_RESTORE_TARGET
  HERMES_BACKUP_READINESS_FILE
  HERMES_NETWORK_READINESS_FILE
)

log() { printf '[hermes-guest] %s\n' "$*"; }
die() { printf '[hermes-guest] ERROR: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Usage: setup-hermes-guest.sh COMMAND --config ABSOLUTE_PATH [ARG]

Commands:
  validate                 Validate the secret-free config and release tuple
  preflight                Validate the Ubuntu guest, tools, CA, key, and endpoint
  install                  Install the pinned NemoClaw CLI without onboarding
  onboard-canary           Create only the synthetic agent-owner sandbox
  health                   Capture redacted doctor, status, route, and version evidence
  snapshot NAME            Create a named snapshot of the synthetic canary
  restore-verify SNAPSHOT  Restore to agent-owner-verify and run health probes

The API key is read from HERMES_LITELLM_API_KEY_FILE. It is never accepted as
an argument or configuration value. This helper does not configure messaging,
web search, MCP, host mounts, or personal data.
EOF
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "Missing required command: $1"
}

require_value() {
  local name="$1"
  [[ -n "${!name:-}" ]] || die "Missing required configuration key: $name"
}

validate_release_manifest() {
  require_command jq
  [[ -f "$RELEASE_MANIFEST" && ! -L "$RELEASE_MANIFEST" ]] ||
    die "Release manifest must be a regular non-symlink: $RELEASE_MANIFEST"
  jq -e \
    --arg commit "$EXPECTED_NEMOCLAW_COMMIT" \
    --arg installer "$EXPECTED_INSTALLER_SHA256" \
    --arg hermes "$EXPECTED_HERMES_VERSION" \
    --arg openshell "$EXPECTED_OPENSHELL_VERSION" \
    '.schema_version == 1 and
     .nemoclaw.commit == $commit and
     .nemoclaw.install_script_sha256 == $installer and
     .managed_components.hermes_version == $hermes and
     .managed_components.openshell_version == $openshell and
     .policy.tier == "restricted" and
     .policy.web_search == "none" and
     .policy.messaging == false and
     .policy.custom_image == false' \
    "$RELEASE_MANIFEST" >/dev/null || die 'Release manifest does not match the reviewed canary tuple'
}

load_runtime_config() {
  local config_path="$1"
  [[ "$config_path" == /* ]] || die 'The configuration path must be absolute'
  load_trusted_env_file "$config_path" "$(id -u)" hermes-runtime-config "${CONFIG_KEYS[@]}" || exit 1
  local key
  for key in "${CONFIG_KEYS[@]}"; do require_value "$key"; done
}

validate_runtime_config() {
  [[ "$HERMES_SANDBOX_NAME" == agent-owner ]] || die 'The pilot sandbox must be agent-owner'
  [[ "$HERMES_RESTORE_TARGET" == agent-owner-verify ]] || die 'The restore target must be agent-owner-verify'
  [[ "$HERMES_MODEL" == assistant-canary ]] || die 'The pilot model must be assistant-canary'
  [[ "$HERMES_ENDPOINT_URL" == https://ai.home.arpa/v1 ]] ||
    die 'The pilot endpoint must be https://ai.home.arpa/v1'
  [[ "$HERMES_TRUSTED_PRIVATE_HOSTS" == ai.home.arpa ]] ||
    die 'Only ai.home.arpa may be trusted as the pilot private host'
  local path_variable
  for path_variable in HERMES_CA_BUNDLE HERMES_LITELLM_API_KEY_FILE HERMES_EVIDENCE_DIR \
    HERMES_BACKUP_READINESS_FILE HERMES_NETWORK_READINESS_FILE; do
    [[ "${!path_variable}" == /* ]] || die "$path_variable must be an absolute path"
  done
  [[ "$HERMES_CA_BUNDLE" != "$HERMES_LITELLM_API_KEY_FILE" ]] ||
    die 'CA bundle and API key paths must differ'
  validate_release_manifest
}

validate_readiness_gate() {
  local path="$1" expected_gate="$2"
  validate_private_file "$path" "$expected_gate readiness record" 8#022
  (( $(wc -c <"$path") <= 16384 )) || die "$expected_gate readiness record is too large"
  jq -e --arg gate "$expected_gate" \
    '.schema_version == 1 and
     .gate == $gate and
     .status == "ready" and
     .scope == "hermes-synthetic-canary" and
     (.observed_at | type == "string" and
       test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")) and
     (.evidence | type == "string" and length > 0 and length <= 2048)' \
    "$path" >/dev/null || die "$expected_gate readiness record is invalid or not ready"
}

validate_backup_readiness_gate() {
  local path="$1" now
  validate_private_file "$path" 'backup readiness record' 8#022
  (( $(wc -c <"$path") <= 16384 )) || die 'backup readiness record is too large'

  # The durable production gate keeps its existing schema and behavior.
  if jq -e \
    '.schema_version == 1 and
     .gate == "off-host-backup" and
     .status == "ready" and
     .scope == "hermes-synthetic-canary" and
     (.observed_at | type == "string" and
       test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")) and
     (.evidence | type == "string" and length > 0 and length <= 2048)' \
    "$path" >/dev/null; then
    log 'Durable off-host backup readiness record is present'
    return 0
  fi

  # The local exception is deliberately non-general: its exact schema binds it
  # to this synthetic owner canary, the canary route, and zero integrations.
  # UTC timestamps in this fixed-width format compare chronologically as text.
  now="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  jq -e --arg now "$now" '
    (keys | sort) == ([
      "allowed_model",
      "allowed_sandbox",
      "data_classification",
      "durability",
      "expires_at",
      "gate",
      "integrations",
      "limitations",
      "observed_at",
      "restore_evidence",
      "risk_acknowledged",
      "schema_version",
      "scope",
      "snapshot_id",
      "status"
    ] | sort) and
    .schema_version == 1 and
    .gate == "local-bootstrap-backup" and
    .status == "ready" and
    .scope == "hermes-synthetic-canary" and
    .durability == "same-host-same-disk" and
    .risk_acknowledged == true and
    .data_classification == "synthetic-only" and
    .allowed_sandbox == "agent-owner" and
    .allowed_model == "assistant-canary" and
    .integrations == "none" and
    (.observed_at | type == "string" and
      test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$")) and
    (.expires_at | type == "string" and
      test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$") and . > $now) and
    (.snapshot_id | type == "string" and
      test("^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")) and
    (.restore_evidence | type == "string" and length > 0 and length <= 2048) and
    (.limitations | type == "string" and length > 0 and length <= 2048)
  ' "$path" >/dev/null ||
    die 'backup readiness record is neither a ready off-host backup nor an unexpired synthetic-only local bootstrap backup'
  log 'Temporary same-host/same-disk backup exception is active for synthetic agent-owner only'
}

require_mutation_gates() {
  validate_backup_readiness_gate "$HERMES_BACKUP_READINESS_FILE"
  validate_readiness_gate "$HERMES_NETWORK_READINESS_FILE" agents-network
  log 'Backup and network readiness records are present'
}

validate_private_file() {
  local path="$1" label="$2" maximum_mode="$3" mode mode_decimal
  [[ -f "$path" && ! -L "$path" && -s "$path" ]] || die "$label must be a non-empty regular non-symlink: $path"
  [[ "$(config_stat_uid "$path")" == "$(id -u)" ]] || die "$label must be owned by the guest operator"
  mode="$(config_stat_mode "$path")"
  mode_decimal=$((8#$mode))
  (( (mode_decimal & maximum_mode) == 0 )) || die "$label permissions are too broad: $mode"
}

validate_ca_bundle() {
  validate_private_file "$HERMES_CA_BUNDLE" 'CA bundle' 8#022
  openssl crl2pkcs7 -nocrl -certfile "$HERMES_CA_BUNDLE" 2>/dev/null |
    openssl pkcs7 -print_certs -noout >/dev/null 2>&1 || die 'CA bundle is not parseable PEM certificate data'
  openssl x509 -in "$HERMES_CA_BUNDLE" -noout -text 2>/dev/null |
    grep -Eq 'CA:TRUE' || die 'CA bundle does not begin with a CA certificate'
}

validate_api_key_file() {
  validate_private_file "$HERMES_LITELLM_API_KEY_FILE" 'LiteLLM API key file' 8#077
  local bytes lines
  bytes="$(wc -c <"$HERMES_LITELLM_API_KEY_FILE")"
  lines="$(wc -l <"$HERMES_LITELLM_API_KEY_FILE")"
  ((bytes >= 16 && bytes <= 4096)) || die 'LiteLLM API key must contain 16 through 4096 bytes'
  ((lines <= 1)) || die 'LiteLLM API key file must contain exactly one logical line'
  LC_ALL=C grep -q '[[:cntrl:]]' "$HERMES_LITELLM_API_KEY_FILE" &&
    die 'LiteLLM API key contains control characters other than its final newline'
  LC_ALL=C grep -Eq '^[A-Za-z0-9._-]+$' "$HERMES_LITELLM_API_KEY_FILE" ||
    die 'LiteLLM API key contains characters unsafe for the credential handoff'
  return 0
}

validate_ubuntu_guest() {
  [[ -f "$OS_RELEASE_FILE" ]] || die "Missing OS release metadata: $OS_RELEASE_FILE"
  local os_id os_version
  os_id="$(sed -n 's/^ID=//p' "$OS_RELEASE_FILE" | tr -d '"' | head -1)"
  os_version="$(sed -n 's/^VERSION_ID=//p' "$OS_RELEASE_FILE" | tr -d '"' | head -1)"
  [[ "$os_id" == ubuntu && "$os_version" == 24.04 ]] ||
    die "This runtime is qualified only for an Ubuntu 24.04 guest (found ${os_id:-unknown} ${os_version:-unknown})"
}

version_at_least() {
  local actual="$1" minimum="$2"
  [[ "$(printf '%s\n%s\n' "$minimum" "$actual" | sort -V | head -1)" == "$minimum" ]]
}

preflight_runtime() {
  validate_ubuntu_guest
  local command
  for command in curl docker getent git grep jq node npm openssl python3 sha256sum sort stat; do require_command "$command"; done
  docker info >/dev/null 2>&1 || die 'Docker is not available to the unprivileged guest operator'
  version_at_least "$(node --version | sed 's/^v//')" 22.19.0 || die 'Node.js 22.19 or newer is required'
  version_at_least "$(npm --version)" 10.0.0 || die 'npm 10 or newer is required'
  validate_ca_bundle
  validate_api_key_file
  getent hosts ai.home.arpa >/dev/null 2>&1 || die 'ai.home.arpa does not resolve inside the guest'
  local api_key models_response
  api_key="$(read_api_key)"
  # Feed the bearer header through curl's stdin config. The secret never
  # appears in argv, a generated file, or command output.
  models_response="$(
    printf 'header = "Authorization: Bearer %s"\n' "$api_key" |
      curl --config - --fail --silent --show-error --connect-timeout 5 --max-time 15 \
        --max-filesize 65536 --cacert "$HERMES_CA_BUNDLE" "$HERMES_ENDPOINT_URL/models"
  )" ||
    die 'The guest cannot validate the private LiteLLM models endpoint'
  api_key=''
  unset api_key
  jq -e --arg model "$HERMES_MODEL" '.data | arrays | any(.id == $model)' \
    <<<"$models_response" >/dev/null || die "The models endpoint does not advertise $HERMES_MODEL"
  models_response=''
  unset models_response
  log 'Preflight passed for the synthetic owner canary'
}

validate_installed_cli_version() {
  require_command nemohermes
  local output
  output="$(nemohermes --version)" || die 'Cannot read the installed NemoClaw version'
  grep -Eq "(^|[^0-9])${EXPECTED_NEMOCLAW_VERSION}([^0-9]|$)" <<<"$output" ||
    die "Installed NemoClaw does not match ${EXPECTED_NEMOCLAW_VERSION}"
}

download_pinned_installer() {
  local destination="$1"
  local url="https://raw.githubusercontent.com/NVIDIA/NemoClaw/${EXPECTED_NEMOCLAW_COMMIT}/install.sh"
  curl --proto '=https' --tlsv1.2 --fail --silent --show-error --location \
    --connect-timeout 15 --max-time 120 --output "$destination" "$url"
  printf '%s  %s\n' "$EXPECTED_INSTALLER_SHA256" "$destination" | sha256sum --check --status ||
    die 'Pinned NemoClaw installer hash mismatch'
}

install_nemoclaw() {
  preflight_runtime
  require_mutation_gates
  local temporary_dir installer
  temporary_dir="$(mktemp -d "${TMPDIR:-/tmp}/homecompute-nemoclaw.XXXXXX")"
  trap 'rm -rf -- "${temporary_dir:-}"' RETURN
  installer="$temporary_dir/install.sh"
  download_pinned_installer "$installer"
  log "Installing NemoClaw ${EXPECTED_NEMOCLAW_VERSION} from the reviewed commit without onboarding"
  env \
    -u COMPATIBLE_API_KEY \
    -u DISCORD_BOT_TOKEN \
    -u NEMOCLAW_PROVIDER \
    -u NEMOCLAW_PROVIDER_KEY \
    -u NVIDIA_API_KEY \
    -u NVIDIA_INFERENCE_API_KEY \
    -u TAVILY_API_KEY \
    NEMOCLAW_INSTALL_REF="$EXPECTED_NEMOCLAW_COMMIT" \
    NEMOCLAW_AGENT=hermes \
    NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1 \
    bash "$installer" --defer-onboarding --yes-i-accept-third-party-software
  rm -rf -- "$temporary_dir"
  trap - RETURN
}

read_api_key() {
  local key
  IFS= read -r key <"$HERMES_LITELLM_API_KEY_FILE" || [[ -n "$key" ]] || die 'Cannot read LiteLLM API key'
  [[ -n "$key" ]] || die 'LiteLLM API key is empty'
  printf '%s' "$key"
}

onboard_canary() {
  preflight_runtime
  require_mutation_gates
  validate_installed_cli_version
  local api_key
  api_key="$(read_api_key)"
  log 'Onboarding the synthetic owner canary with Restricted policy and no integrations'
  env \
    -u BRAVE_API_KEY \
    -u DISCORD_BOT_TOKEN \
    -u SLACK_APP_TOKEN \
    -u SLACK_BOT_TOKEN \
    -u TELEGRAM_BOT_TOKEN \
    -u TAVILY_API_KEY \
    -u WHATSAPP_ACCESS_TOKEN \
    COMPATIBLE_API_KEY="$api_key" \
    NEMOCLAW_ACCEPT_THIRD_PARTY_SOFTWARE=1 \
    NEMOCLAW_AGENT=hermes \
    NEMOCLAW_CORPORATE_CA_BUNDLE="$HERMES_CA_BUNDLE" \
    NEMOCLAW_ENDPOINT_URL="$HERMES_ENDPOINT_URL" \
    NEMOCLAW_MODEL="$HERMES_MODEL" \
    NEMOCLAW_NON_INTERACTIVE=1 \
    NEMOCLAW_POLICY_MODE=suggested \
    NEMOCLAW_POLICY_TIER=restricted \
    NEMOCLAW_PROVIDER=custom \
    NEMOCLAW_SANDBOX_NAME="$HERMES_SANDBOX_NAME" \
    NEMOCLAW_TRUSTED_PRIVATE_HOSTS="$HERMES_TRUSTED_PRIVATE_HOSTS" \
    NEMOCLAW_WEB_SEARCH_PROVIDER=none \
    nemohermes onboard --non-interactive --yes-i-accept-third-party-software
  api_key=''
  unset api_key
}

new_evidence_directory() {
  local operation="$1" stamp directory
  install -d -m 0700 "$HERMES_EVIDENCE_DIR"
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  directory="$HERMES_EVIDENCE_DIR/${stamp}-${operation}"
  mkdir -m 0700 "$directory"
  printf '%s' "$directory"
}

capture() {
  local directory="$1" label="$2"
  shift 2
  "$@" >"$directory/$label.stdout" 2>"$directory/$label.stderr" || {
    log "Probe failed; evidence retained in $directory"
    return 1
  }
}

assert_runtime_versions() {
  local directory="$1"
  local status_json="$directory/sandbox-status.stdout"
  jq -e \
    --arg openshell "$EXPECTED_OPENSHELL_VERSION" \
    --arg model "$HERMES_MODEL" \
    '.agent == "hermes" and
     .phase == "Ready" and
     .openshellVersion == $openshell and
     .inferenceHealth.ok == true and
     ((.recordedRoute.model // .model) == $model)' "$status_json" >/dev/null ||
    die "Sandbox status does not match the reviewed runtime tuple; inspect $directory"
  grep -Eq "(^|[^0-9])${EXPECTED_HERMES_VERSION}([^0-9]|$)" "$directory/hermes-version.stdout" ||
    die "Sandbox Hermes version is not ${EXPECTED_HERMES_VERSION}; inspect $directory"
  grep -Eq "(^|[^0-9])${EXPECTED_NEMOCLAW_VERSION}([^0-9]|$)" "$directory/nemoclaw-version.stdout" ||
    die "Host NemoClaw version is not ${EXPECTED_NEMOCLAW_VERSION}; inspect $directory"
}

assert_evidence_excludes_api_key() {
  local directory="$1" api_key
  validate_api_key_file
  api_key="$(read_api_key)"
  if grep -R -F -q -- "$api_key" "$directory"; then
    api_key=''
    unset api_key
    die "LiteLLM credential appeared in captured evidence: $directory"
  fi
  api_key=''
  unset api_key
}

health_for() {
  local sandbox="$1" operation="$2" directory
  require_command jq
  require_command nemohermes
  directory="$(new_evidence_directory "$operation")"
  capture "$directory" nemoclaw-version nemohermes --version
  capture "$directory" doctor nemohermes doctor --json
  capture "$directory" sandbox-status nemohermes "$sandbox" status --json
  capture "$directory" inference-route nemohermes "$sandbox" inference get --json
  capture "$directory" hermes-version nemohermes "$sandbox" exec -- hermes --version
  capture "$directory" connect-probe nemohermes "$sandbox" connect --probe-only
  assert_runtime_versions "$directory"
  cp -- "$RELEASE_MANIFEST" "$directory/release-manifest.json"
  assert_evidence_excludes_api_key "$directory"
  chmod 0600 "$directory"/*
  log "Health evidence written to $directory"
}

validate_snapshot_name() {
  [[ "$1" =~ ^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$ ]] ||
    die 'Snapshot name must be 1-80 safe ASCII characters'
}

create_snapshot() {
  local name="$1"
  validate_snapshot_name "$name"
  require_mutation_gates
  validate_installed_cli_version
  nemohermes "$HERMES_SANDBOX_NAME" snapshot create --name "$name"
}

restore_verify() {
  local snapshot="$1" inventory
  validate_snapshot_name "$snapshot"
  require_mutation_gates
  require_command jq
  validate_installed_cli_version
  inventory="$(nemohermes list --json)"
  if jq -e --arg name "$HERMES_RESTORE_TARGET" '[.. | objects | .name? // empty] | index($name) != null' \
    <<<"$inventory" >/dev/null; then
    die "Restore target already exists; refusing to replace it: $HERMES_RESTORE_TARGET"
  fi
  log "Restoring snapshot into disposable verification target $HERMES_RESTORE_TARGET"
  nemohermes "$HERMES_SANDBOX_NAME" snapshot restore "$snapshot" --to "$HERMES_RESTORE_TARGET"
  health_for "$HERMES_RESTORE_TARGET" restore-verify
  log "Verification target was intentionally retained for operator inspection: $HERMES_RESTORE_TARGET"
}

main() {
  local command="${1:-}" config_path='' argument=''
  [[ -n "$command" ]] || { usage; exit 2; }
  shift
  while (($#)); do
    case "$1" in
      --config)
        (($# >= 2)) || die '--config requires a path'
        config_path="$2"
        shift 2
        ;;
      --help|-h)
        usage
        return 0
        ;;
      *)
        [[ -z "$argument" ]] || die "Unexpected argument: $1"
        argument="$1"
        shift
        ;;
    esac
  done
  [[ -n "$config_path" ]] || die '--config is required'
  load_runtime_config "$config_path"
  validate_runtime_config
  case "$command" in
    validate) [[ -z "$argument" ]] || die 'validate takes no argument'; log 'Configuration and release tuple are valid' ;;
    preflight) [[ -z "$argument" ]] || die 'preflight takes no argument'; preflight_runtime ;;
    install) [[ -z "$argument" ]] || die 'install takes no argument'; install_nemoclaw ;;
    onboard-canary) [[ -z "$argument" ]] || die 'onboard-canary takes no argument'; onboard_canary ;;
    health) [[ -z "$argument" ]] || die 'health takes no argument'; health_for "$HERMES_SANDBOX_NAME" health ;;
    snapshot) [[ -n "$argument" ]] || die 'snapshot requires NAME'; create_snapshot "$argument" ;;
    restore-verify) [[ -n "$argument" ]] || die 'restore-verify requires SNAPSHOT'; restore_verify "$argument" ;;
    help) usage ;;
    *) usage; die "Unknown command: $command" ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
