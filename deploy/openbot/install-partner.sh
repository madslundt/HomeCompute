#!/usr/bin/env bash
# Install the reviewed partner bundle inside the agents VM.
set -Eeuo pipefail
umask 077

source_root="$1"
revision="$2"
[[ "$revision" =~ ^[0-9a-f]{40}$ && $EUID == 0 ]]
readonly project=homecompute-openbot-partner
readonly root="/etc/$project"
readonly data="/var/lib/$project/data"
readonly service="$project.service"
readonly firewall="$project-firewall.service"
readonly release="$root/releases/$revision"
readonly -a assets=(
  deploy/openbot/compose.partner.yaml
  deploy/openbot/seccomp.json
  deploy/openbot/homecompute-openbot-partner.service
  deploy/openbot/homecompute-openbot-partner-firewall.service
  deploy/openbot/homecompute-openbot-health
  deploy/openbot/network-policy.py
  scripts/configure-openbot-hermes.py
  config/openbot-network.json
)

exec 9>"/run/lock/$project.lock"
flock -n 9
for directory in "$root" "$root/releases" "${data%/*}"; do
  [[ ! -L "$directory" ]] || exit 1
done
if [[ -L "$root/current" ]]; then
  previous="$(readlink -f "$root/current")"
  [[ "$previous" == "$root/releases/${previous##*/}" && "${previous##*/}" =~ ^[0-9a-f]{40}$ ]]
  (cd "$previous" && sha256sum --check --strict --quiet SHA256SUMS)
elif [[ -e "$root/current" || -e "$data" || -L "$data" ]]; then
  echo 'Unmanaged partner OpenBot files exist.' >&2
  exit 1
fi

for unit in "$service" "$firewall"; do
  path="/etc/systemd/system/$unit"
  if [[ -e "$path" || -L "$path" ]]; then
    [[ -L "$path" && "$(readlink "$path")" == "$root/current/$unit" ]]
  else
    [[ "$(systemctl show "$unit" -p LoadState --value)" == not-found ]]
  fi
  [[ -z "$(systemctl show "$unit" -p DropInPaths --value)" ]]
done
boot_directory=/etc/systemd/system/multi-user.target.wants
boot="$boot_directory/$service"
[[ ! -L "$boot_directory" ]]
if [[ -e "$boot" || -L "$boot" ]]; then
  [[ -L "$boot" && "$(readlink "$boot")" == "../$service" ]]
fi
if docker container inspect "$project" >/dev/null 2>&1; then
  [[ -n "${previous:-}" ]]
  [[ "$(docker container inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' "$project")" == "$project" ]]
fi
if docker network inspect "$project" >/dev/null 2>&1; then
  [[ -n "${previous:-}" ]]
  [[ "$(docker network inspect --format '{{ index .Labels "com.docker.compose.project" }}' "$project")" == "$project" ]]
elif ip link show hc-ob-partner >/dev/null 2>&1; then
  echo 'Partner OpenBot bridge interface already exists outside its Compose project.' >&2
  exit 1
fi
if [[ -e "$data" || -L "$data" ]]; then
  [[ ! -L "$data" && -d "$data" && "$(stat -c '%a:%u' "$data")" == 700:1000 ]]
fi

install -d -m 0755 "$root" "$root/releases"
stage="$(mktemp -d "$root/releases/.stage.XXXXXXXX")"
trap 'rm -rf -- "$stage"' EXIT
for asset in "${assets[@]}"; do
  install -m 0644 "$source_root/$asset" "$stage/${asset##*/}"
done
(cd "$stage" && sha256sum -- * > SHA256SUMS)
chmod 0755 "$stage"
compose=(docker compose --project-name "$project" --file "$stage/compose.partner.yaml")
"${compose[@]}" config --quiet
image="$("${compose[@]}" config --images)"
[[ "$image" =~ ^ghcr[.]io/nightly-labs/openbot@sha256:[0-9a-f]{64}$ ]]
systemd-analyze verify "$stage/$service" "$stage/$firewall"

if [[ -e "$release" || -L "$release" ]]; then
  [[ -d "$release" && ! -L "$release" ]]
  diff -qr -- "$stage" "$release"
else
  mv -T -- "$stage" "$release"
fi
if [[ "${previous:-}" == "$release" ]] && systemctl --quiet is-active "$service"; then
  /bin/bash "$release/homecompute-openbot-health" "$project"
  exit 0
fi

docker pull --platform linux/amd64 "$image"
if [[ -n "${previous:-}" ]]; then
  systemctl stop "$service"
  [[ "$(docker ps --filter "name=^/$project$" --format '{{.Names}}')" == '' ]]
  systemctl stop "$firewall"
fi
ln -sfnT -- "$release" "$root/current"
if [[ ! -d "$data" ]]; then
  install -d -m 0755 "${data%/*}"
  install -d -m 0700 -o 1000 -g 1000 "$data"
fi
for unit in "$service" "$firewall"; do
  ln -sfnT -- "$root/current/$unit" "/etc/systemd/system/$unit"
done
install -d -m 0755 "$boot_directory"
ln -sfnT -- "../$service" "$boot"
systemctl daemon-reload
if ! systemctl start "$service"; then
  systemctl stop "$service"
  echo 'Partner OpenBot failed to start. Guest data and the selected release were preserved.' >&2
  exit 1
fi
printf 'Partner OpenBot installed from %s. Account enrollment is pending.\n' "$revision"
