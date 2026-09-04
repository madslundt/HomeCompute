#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
temporary_root="$(mktemp -d "${TMPDIR:-/tmp}/homecompute-validation.XXXXXX")"
trap 'rm -rf -- "$temporary_root"' EXIT

require_command() {
  command -v "$1" >/dev/null 2>&1 || {
    printf '[validate] missing required command: %s\n' "$1" >&2
    exit 1
  }
}

for command_name in bash shellcheck jq docker rg; do
  require_command "$command_name"
done
docker compose version >/dev/null

printf '[validate] NixOS/Home Manager boundaries\n'
obsolete_paths=(
  "$REPO_ROOT/scripts/setup-control-plane.sh"
  "$REPO_ROOT/scripts/setup-services-node.sh"
  "$REPO_ROOT/config/services-node.env.example"
  "$REPO_ROOT/deploy/services-node"
)
for obsolete_path in "${obsolete_paths[@]}"; do
  [[ ! -e "$obsolete_path" ]] || {
    printf '[validate] obsolete host provisioning path remains: %s\n' "$obsolete_path" >&2
    exit 1
  }
done
if rg -n '(^|[[:space:]])(boot|fileSystems|networking|services|sops|systemd|users|virtualisation)\.' \
  "$REPO_ROOT/home"; then
  printf '[validate] system-level option found in Home Manager configuration\n' >&2
  exit 1
fi
if rg -n 'homeConfigurations' "$REPO_ROOT/flake.nix"; then
  printf '[validate] standalone Home Manager output is not allowed\n' >&2
  exit 1
fi

shell_files=(
  "$REPO_ROOT/scripts/lib/config.sh"
  "$REPO_ROOT/scripts/setup-compute-node.sh"
  "$REPO_ROOT/scripts/deploy-home-core.sh"
  "$REPO_ROOT/scripts/validate-repository.sh"
  "$REPO_ROOT/tests/config-loader-test.sh"
)

printf '[validate] Bash syntax\n'
bash -n "${shell_files[@]}"
sh -n \
  "$REPO_ROOT/deploy/control-plane/litellm-entrypoint.sh" \
  "$REPO_ROOT/deploy/control-plane/postgres-init.sh"

printf '[validate] ShellCheck\n'
shellcheck "${shell_files[@]}"

printf '[validate] configuration-loader tests\n'
bash "$REPO_ROOT/tests/config-loader-test.sh"

printf '[validate] JSON syntax\n'
while IFS= read -r -d '' json_file; do
  jq empty "$json_file"
done < <(find "$REPO_ROOT/automations" -type f -name '*.json' -print0)

if command -v ruby >/dev/null 2>&1; then
  printf '[validate] YAML syntax\n'
  ruby -e 'require "yaml"; ARGV.each { |path| YAML.safe_load(File.read(path), permitted_classes: [], permitted_symbols: [], aliases: true) }' \
    "$REPO_ROOT/deploy/control-plane/compose.yaml" \
    "$REPO_ROOT/deploy/control-plane/litellm-config.yaml" \
    "$REPO_ROOT/deploy/homepage/compose.yaml" \
    "$REPO_ROOT/deploy/homepage/config/services.yaml" \
    "$REPO_ROOT/deploy/homepage/config/settings.yaml" \
    "$REPO_ROOT/deploy/homepage/config/widgets.yaml" \
    "$REPO_ROOT/deploy/homepage/config/proxmox.yaml" \
    "$REPO_ROOT/.github/workflows/validate.yml"
else
  printf '[validate] Ruby not installed; YAML syntax check skipped\n'
fi

secret_file="$temporary_root/dummy-secret"
printf 'validation-only\n' >"$secret_file"
chmod 0600 "$secret_file"
compose_env="$temporary_root/compute.env"
cat >"$compose_env" <<EOF
VLLM_IMAGE=example.invalid/vllm@sha256:0000000000000000000000000000000000000000000000000000000000000000
MODEL_ID=nvidia/Qwen3.6-35B-A3B-NVFP4
MODEL_REVISION=1111111111111111111111111111111111111111
TOKENIZER_REVISION=1111111111111111111111111111111111111111
CODE_REVISION=1111111111111111111111111111111111111111
CHAT_TEMPLATE_SHA256=2222222222222222222222222222222222222222222222222222222222222222
GB10_ROOT=$temporary_root/runtime
GB10_RUNTIME_UID=1000
GB10_RUNTIME_GID=1000
GB10_BIND_ADDRESS=127.0.0.1
VLLM_HOST_PORT=8000
HF_TOKEN_FILE=$secret_file
VLLM_API_KEY_FILE=$secret_file
VLLM_MAX_MODEL_LEN=32768
VLLM_MAX_NUM_SEQS=2
VLLM_MAX_BATCHED_TOKENS=8192
VLLM_GPU_MEMORY_UTILIZATION=0.40
VLLM_SHM_SIZE=16gb
VLLM_ATTENTION_BACKEND=flashinfer
VLLM_MOE_BACKEND=marlin
VLLM_REASONING_PARSER=qwen3
VLLM_TOOL_CALL_PARSER=qwen3_xml
VLLM_SPECULATIVE_CONFIG=
EOF

printf '[validate] Compose rendering\n'
docker compose --env-file "$compose_env" \
  -f "$REPO_ROOT/deploy/compute-node/compose.yaml" config --quiet
docker compose --env-file "$compose_env" --profile prepare \
  -f "$REPO_ROOT/deploy/compute-node/compose.yaml" config --quiet

control_plane_env="$temporary_root/control-plane.env"
control_plane_state="$temporary_root/state/control-plane"
mkdir -p \
  "$control_plane_state/caddy-data" \
  "$control_plane_state/caddy-config" \
  "$control_plane_state/postgres-data"
cat >"$control_plane_env" <<EOF
TIMEZONE=Europe/Copenhagen
CONTROL_PLANE_STATE_ROOT=$control_plane_state
CONTROL_PLANE_SECRET_GID=1
CONTROL_PLANE_EDGE_SUBNET=172.28.200.0/24
CADDY_EDGE_IP=172.28.200.2
LITELLM_EDGE_IP=172.28.200.3
CONTROL_PLANE_BIND_ADDRESS=127.0.0.1
CONTROL_PLANE_HTTPS_PORT=8443
AI_FQDN=ai.home.arpa
CADDY_IMAGE=example.invalid/caddy@sha256:0000000000000000000000000000000000000000000000000000000000000000
LITELLM_IMAGE=example.invalid/litellm@sha256:0000000000000000000000000000000000000000000000000000000000000000
POSTGRES_IMAGE=example.invalid/postgres@sha256:0000000000000000000000000000000000000000000000000000000000000000
COMPUTE_OPENAI_BASE_URL=https://10.77.10.10:8000/v1
COMPUTE_API_KEY_FILE=$secret_file
LITELLM_MASTER_KEY_FILE=$secret_file
LITELLM_SALT_KEY_FILE=$secret_file
POSTGRES_ADMIN_PASSWORD_FILE=$secret_file
POSTGRES_APP_PASSWORD_FILE=$secret_file
EOF
docker compose --env-file "$control_plane_env" \
  -f "$REPO_ROOT/deploy/control-plane/compose.yaml" config --quiet
control_plane_json="$temporary_root/control-plane.json"
docker compose --env-file "$control_plane_env" \
  -f "$REPO_ROOT/deploy/control-plane/compose.yaml" config --format json >"$control_plane_json"
jq -e '
  ((.services | keys) == ["caddy", "litellm", "postgres"]) and
  all(.services[]; (.privileged // false) == false and (.network_mode // "") != "host") and
  all(.services[]; ((.devices // []) | length) == 0) and
  ([.services[].volumes[]? | select(.type == "bind" and .read_only != true) | .target] | sort) ==
    ["/config", "/data", "/var/lib/postgresql/data"] and
  ([.services[].volumes[]? | select(.type == "bind") | .target] | sort) ==
    ["/config", "/data", "/docker-entrypoint-initdb.d/10-litellm-role.sh", "/etc/caddy/Caddyfile", "/etc/litellm/config.yaml", "/opt/homecompute/litellm-entrypoint.sh", "/var/lib/postgresql/data"] and
  (.services.caddy.ports | length == 1) and
  (.services.caddy.ports[0].host_ip == "127.0.0.1") and
  (.services.caddy.ports[0].protocol == "tcp") and
  (.services.litellm.ports == null) and
  (.services.postgres.ports == null) and
  (.services.postgres.user == "70:70") and
  (.services.postgres.cap_add == null) and
  (.services.postgres.cap_drop | index("ALL") != null) and
  (.services.caddy.cap_add == ["NET_BIND_SERVICE"]) and
  (.networks.state.internal == true) and
  (.services.caddy.networks.state == null) and
  (.services.postgres.networks.edge == null)
' "$control_plane_json" >/dev/null
printf '[validate] Application project isolation (ADR-017)\n'
automation_json="$temporary_root/automation-compose.json"
docker compose --env-file "$REPO_ROOT/config/automation.env.example" \
  -f "$REPO_ROOT/deploy/automation/compose.yaml" config --format json >"$automation_json"
jq -e '
  (.services.n8n.user == "1000:1000") and
  (.services.n8n.read_only == true) and
  (.services.n8n.cap_drop | index("ALL") != null) and
  (.services.n8n.security_opt | index("no-new-privileges:true") != null) and
  (.services.n8n.ports == null) and
  (.services["aula-mcp"] == null) and
  (.services.n8n.mem_limit != null) and
  (.services.n8n.cpus != null) and
  (.networks.migration.internal == true)
' "$automation_json" >/dev/null
homepage_json="$temporary_root/homepage.json"
docker compose --env-file "$REPO_ROOT/config/homepage.env.example" \
  -f "$REPO_ROOT/deploy/homepage/compose.yaml" config --format json >"$homepage_json"
jq -e '
  ((.services | keys) == ["homepage"]) and
  (.services.homepage.image | test("@sha256:[0-9a-f]{64}$")) and
  (.services.homepage.user == "1000:1000") and
  (.services.homepage.read_only == true) and
  (.services.homepage.cap_drop | index("ALL") != null) and
  (.services.homepage.security_opt | index("no-new-privileges:true") != null) and
  (.services.homepage.ports | length == 3) and
  ([.services.homepage.ports[].host_ip] | sort == ["100.110.248.102", "127.0.0.1", "192.168.30.122"]) and
  all(.services.homepage.ports[]; .published == "80" and .target == 3000 and .protocol == "tcp") and
  all(.services.homepage.volumes[]; .type == "bind" and .read_only == true) and
  (.services.homepage.mem_limit > 0 and .services.homepage.cpus > 0 and .services.homepage.pids_limit > 0)
' "$homepage_json" >/dev/null
docker compose --env-file "$REPO_ROOT/config/automation.env.example" \
  -f "$REPO_ROOT/deploy/automation/compose.yaml" \
  -f "$REPO_ROOT/deploy/automation/production.yaml" config --format json >"$automation_json"
jq -e '
  (.services.n8n.ports | length == 3) and
  ([.services.n8n.ports[].host_ip] | sort == ["100.110.248.102", "127.0.0.1", "192.168.30.122"]) and
  (.networks.migration.internal != true) and
  (.networks.migration.enable_ipv6 == false) and
  (.networks.migration.driver_opts["com.docker.network.bridge.name"] == "br-hc-n8n")
' "$automation_json" >/dev/null
jq -e '
  .services["aula-mcp"] |
  (.ports | length == 1) and
  (.ports[0].host_ip == "127.0.0.1" and .ports[0].target == 7878) and
  (.networks.migration.ipv4_address == "172.28.201.3") and
  (.networks | keys == ["migration"]) and
  (.user == "1000:1000" and .read_only == true) and
  (.cap_drop | index("ALL") != null) and
  (.security_opt | index("no-new-privileges:true") != null) and
  (.environment.AULA_MCP_WRITE == "0" and .environment.AULA_MCP_RAW == "0") and
  (.environment.AULA_MCP_HTTP_MAX_SESSIONS == "32") and
  (.environment.AULA_MCP_HTTP_IDLE_MS == "60000") and
  (.environment.AULA_MCP_INGRESS_PORT == null) and
  (.volumes | length == 1) and
  (.volumes[0].source == "/srv/state/automation/aula-mcp" and .volumes[0].target == "/data") and
  (.mem_limit > 0 and .cpus > 0 and .pids_limit > 0)
' "$automation_json" >/dev/null
# Books remains staged until source data and cutover are reviewed. Validate its
# own private bindings, resource limits, and image pins before accepting it.
books_json="$temporary_root/books.json"
docker compose --env-file "$REPO_ROOT/config/books_importer.env.example" \
  --env-file "$REPO_ROOT/config/books_importer-secrets.env.example" \
  -f "$REPO_ROOT/deploy/books_importer/compose.yaml" config --format json >"$books_json"
jq -e '
  ((.services | keys) == ["cwa", "shelfmark", "shelfmark-automated"]) and
  all(.services[];
    (.image | test("@sha256:[0-9a-f]{64}$")) and
    (.mem_limit > 0) and (.cpus > 0) and (.pids_limit > 0) and
    (.security_opt | index("no-new-privileges:true") != null) and
    (.logging.driver == "local") and
    all(.volumes[]; .type == "bind" and (.source | startswith("/srv/state/books_importer/")))) and
  ([.services[].ports[]?.host_ip] == ["127.0.0.1", "127.0.0.1"]) and
  (.services["shelfmark-automated"].ports == null) and
  (.networks.default.driver_opts["com.docker.network.bridge.host_binding_ipv4"] == "127.0.0.1")
' "$books_json" >/dev/null

# ADR-017 puts the gateway, automations, and agent sandboxes on one kernel, so
# per-project container controls are the only boundary left between them. Each
# pattern below removes that boundary outright rather than weakening it, so the
# scan covers every deployment directory instead of only the gateway. It is
# restricted to Compose files so prose describing these risks does not trip it.
if rg -n -g '*.yaml' \
  'docker\.sock|privileged:[[:space:]]*true|network_mode:[[:space:]]*host|pid:[[:space:]]*host|ipc:[[:space:]]*host' \
  "$REPO_ROOT/deploy"; then
  printf '[validate] forbidden capability, namespace, or socket reference under deploy/\n' >&2
  exit 1
fi

# Adding a project here is the point at which its isolation gets reviewed.
# Without this check a new deploy/<name>/compose.yaml would inherit none of the
# service-level assertions above, and ADR-017's controls would quietly become
# documentation of an arrangement that no longer exists.
expected_deployment_projects="$(printf '%s\n' automation books_importer compute-node control-plane homepage | LC_ALL=C sort)"
actual_deployment_projects="$(
  cd "$REPO_ROOT/deploy" && find . -mindepth 1 -maxdepth 1 -type d |
    sed 's|^\./||' | LC_ALL=C sort
)"
if [[ "$expected_deployment_projects" != "$actual_deployment_projects" ]]; then
  printf '[validate] deploy/ project list changed; review isolation controls and update this check\n' >&2
  printf '[validate] expected: %s\n' "$(printf '%s' "$expected_deployment_projects" | tr '\n' ' ')" >&2
  printf '[validate] found:    %s\n' "$(printf '%s' "$actual_deployment_projects" | tr '\n' ' ')" >&2
  exit 1
fi

if command -v d2 >/dev/null 2>&1; then
  printf '[validate] D2 syntax/rendering\n'
  while IFS= read -r -d '' d2_file; do
    d2 "$d2_file" "$temporary_root/$(basename "${d2_file%.d2}").svg"
  done < <(find "$REPO_ROOT/diagrams" -maxdepth 1 -type f -name '*.d2' -print0)
else
  printf '[validate] D2 not installed; diagram rendering skipped\n'
fi

if command -v nix >/dev/null 2>&1; then
  printf '[validate] Nix flake evaluation\n'
  nix --extra-experimental-features 'nix-command flakes' flake check \
    "path:$REPO_ROOT" --no-build --all-systems
else
  printf '[validate] Nix flake evaluation (containerized)\n'
  docker run --rm \
    -v "$REPO_ROOT:/src:ro" \
    -w /src \
    nixos/nix:2.34.1@sha256:1d59121e0c361076b4f23c158d236702f2f045b3b477b51075b81ceb6188d34a \
    nix --extra-experimental-features 'nix-command flakes' flake check \
      path:/src --no-build --all-systems
fi

printf '[validate] PASS\n'
