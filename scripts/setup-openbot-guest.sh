#!/usr/bin/env bash
# Install the source-pinned OpenBot service into the isolated agents VM.
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
readonly SCRIPT_DIR REPO_ROOT
readonly GUEST="hermes-operator@10.77.20.2"
readonly KEY_FILE="${OPENBOT_GUEST_SSH_KEY:-$HOME/.ssh/id_ed25519_ai-services-01}"
readonly REMOTE_BASE="/home/hermes-operator/.local/share/homecompute-openbot"
readonly ASSETS=(
  "$REPO_ROOT/deploy/openbot/compose.yaml"
  "$REPO_ROOT/deploy/openbot/seccomp.json"
  "$REPO_ROOT/deploy/openbot/homecompute-openbot.service"
  "$REPO_ROOT/deploy/openbot/homecompute-openbot-firewall.service"
  "$REPO_ROOT/deploy/openbot/network-policy.py"
  "$REPO_ROOT/deploy/openbot/homecompute-openbot-health"
  "$REPO_ROOT/scripts/configure-openbot-hermes.py"
  "$REPO_ROOT/config/openbot-network.json"
  "$REPO_ROOT/config/openbot-install.json"
)

usage() {
  cat <<'EOF'
Usage: setup-openbot-guest.sh install|install-scoped|verify-scoped|upgrade-team-image|login|login-operator|login-operator-scoped|status|configure-compression

install  Install or reconcile the pinned service in the agents VM. The current
         checkout must be a clean commit equal to origin/master and Home Core's
         deployed revision. Managed upgrades preserve /data and refuse drift.
         This does not sign in to OpenBot or configure a provider.
install-scoped  Add the isolated operator after verifying the scoped Core rollout
                and encrypted VM backup. The team image and package pin must match.
verify-scoped   Verify the scoped Core gateway and current VM backup.
upgrade-team-image  Apply only the published image pin to the managed team-only release.
status   Read guest systemd and container status without showing account data.
login    Complete the official email-code server sign-in in this terminal.
configure-compression  Apply only the Hermes threshold from current published master.
login-operator  Sign the isolated operator server into the same owner account.
login-operator-scoped  Sign in after verifying the scoped rollout.
EOF
}

ssh_guest() {
  ssh -J home-core -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -i "$KEY_FILE" "$GUEST" "$@"
}

require_clean_master_checkout() {
  [[ -f "$KEY_FILE" && ! -L "$KEY_FILE" ]] || { echo "Missing dedicated guest SSH identity: $KEY_FILE" >&2; return 1; }
  local revision published porcelain
  revision="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  published="$(git -C "$REPO_ROOT" rev-parse origin/master)"
  porcelain="$(git -C "$REPO_ROOT" status --porcelain --untracked-files=all)"
  [[ "$revision" =~ ^[0-9a-f]{40}$ && "$revision" == "$published" && -z "$porcelain" ]] || {
    echo 'Use a clean checkout of the exact published origin/master revision.' >&2; return 1;
  }
}

require_published_release() {
  require_clean_master_checkout
  local revision deployed
  revision="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  deployed="$(ssh home-core 'sudo -n cat /var/lib/homecompute/deployed-revision')"
  [[ "$deployed" == "$revision" ]] || { echo 'Home Core must be deployed from this exact published revision first.' >&2; return 1; }
}

require_current_master_checkout() {
  require_clean_master_checkout
  local revision published
  revision="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  published="$(git -C "$REPO_ROOT" ls-remote --exit-code origin refs/heads/master)"
  [[ "${published%%$'\t'*}" == "$revision" ]] || {
    echo 'Fetch origin/master and check out its current published revision first.' >&2; return 1;
  }
}

require_scoped_release() {
  require_current_master_checkout
  local revision release
  revision="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  release="/srv/homecompute/releases/$revision"
  ssh -o BatchMode=yes -o StrictHostKeyChecking=yes home-core \
    "sudo -n python3 $release/scripts/deploy-openbot-scoped-gateway.py verify --revision $revision && sudo -n python3 $release/scripts/backup-openbot-agents-vm.py verify --revision $revision"
}

configure_compression() {
  require_current_master_checkout
  local revision
  revision="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  git -C "$REPO_ROOT" show "$revision:scripts/configure-openbot-hermes.py" |
    ssh_guest python3 - --compression-only
}

image_pin() {
  git -C "$REPO_ROOT" show "$1:deploy/openbot/compose.yaml" |
    awk '$1 == "image:" && !found++ { print $2 }'
}

upgrade_team_image() {
  require_current_master_checkout
  local revision previous base old_image new_image asset source filename checksum manifest
  local pin_pattern='^ghcr[.]io/nightly-labs/openbot@sha256:[0-9a-f]{64}$'
  revision="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  local installed
  # shellcheck disable=SC2016 # The path is resolved on the guest.
  installed="$(ssh_guest '
    directory=$(readlink -f ~/.local/share/homecompute-openbot/current) || exit 1
    base=${directory##*/}
    if test -f "$directory/image-base-revision"; then
      base=$(cat "$directory/image-base-revision") || exit 1
    fi
    printf "%s %s\n" "${directory##*/}" "$base"
  ')"
  IFS=' ' read -r previous base <<< "$installed"
  [[ "$previous" =~ ^[0-9a-f]{40}$ && "$base" =~ ^[0-9a-f]{40}$ ]] || return 1
  # The original team-only release may be on a historical production branch.
  git -C "$REPO_ROOT" fetch --no-tags origin "$base"
  old_image="$(image_pin "$previous")"
  new_image="$(image_pin "$revision")"
  [[ "$old_image" =~ $pin_pattern && "$new_image" =~ $pin_pattern ]] || {
    echo 'Both OpenBot releases must have an immutable image pin.' >&2; return 1;
  }
  if [[ "$previous" == "$base" ]]; then
    git -C "$REPO_ROOT" show "$base:deploy/openbot/homecompute-openbot-health" |
      grep -Fx 'readonly CONTAINER=homecompute-openbot' >/dev/null || {
      echo 'The selected base release does not manage a single OpenBot team server.' >&2
      return 1
    }
  fi
  manifest=''
  for asset in "${ASSETS[@]}"; do
    source="${asset#"$REPO_ROOT/"}"
    filename="${asset##*/}"
    checksum="$(
      git -C "$REPO_ROOT" show "$base:$source" |
        render_team_pin "$filename" "$old_image" |
        shasum -a 256
    )"
    manifest+="${checksum%% *}  $filename"$'\n'
  done
  # Pass a single shell-safe token to preserve the manifest across SSH.
  manifest="$(printf '%s' "$manifest" | base64 | tr -d '\n')"
  ssh_guest bash -s -- "$REMOTE_BASE" "$previous" "$base" "$revision" "$old_image" "$new_image" "$manifest" <<'REMOTE_IMAGE'
set -Eeuo pipefail
root="$1"; previous="$2"; base="$3"; revision="$4"; old_image="$5"; image="$6"
image_releases="$root/releases/team-image"
old="$root/releases/$previous"
if [[ "$previous" != "$base" ]]; then
  old="$image_releases/$previous"
fi
release="$image_releases/$revision"
exec 9>"$root/image-upgrade.lock"
flock -n 9
test -L "$root/current"
test "$(readlink -f "$root/current")" = "$old"
cd "$old"
printf '%s' "$7" | base64 -d | sha256sum --check --strict --quiet
cmp -s homecompute-openbot.service /etc/systemd/system/homecompute-openbot.service
cmp -s homecompute-openbot-firewall.service /etc/systemd/system/homecompute-openbot-firewall.service
cmp -s openbot-network.json /etc/homecompute-openbot/network.json
cmp -s network-policy.py /usr/local/libexec/homecompute-openbot-network-policy
cmp -s homecompute-openbot-health /usr/local/libexec/homecompute-openbot-health
test -z "$(systemctl show homecompute-openbot.service -p DropInPaths --value)"
test "$(docker compose --file compose.yaml config --services)" = openbot
operator="$(docker ps -a --filter 'name=^/homecompute-openbot-operator$' --format '{{.Names}}')"
test -z "$operator"
test "$(docker inspect --format '{{.Config.Image}}' homecompute-openbot)" = "$old_image"
sudo -n /usr/local/libexec/homecompute-openbot-health
if [[ "$old_image" == "$image" ]]; then
  echo 'OpenBot is already running the published image.'
  exit 0
fi

install -d -m 0700 "$image_releases"
stage="$(mktemp -d "$image_releases/.stage.XXXXXX")"
restore_before_start=false
cleanup() {
  if "$restore_before_start"; then
    ln -sfnT -- "$old" "$root/current" &&
      sudo -n install -o root -g root -m 0755 "$old/homecompute-openbot-health" /usr/local/libexec/homecompute-openbot-health &&
      sudo -n systemctl start homecompute-openbot.service ||
        echo 'Could not resume the previous OpenBot release; inspect the guest service.' >&2
  fi
  rm -rf -- "$stage"
}
trap cleanup EXIT
for file in compose.yaml seccomp.json homecompute-openbot.service homecompute-openbot-firewall.service network-policy.py homecompute-openbot-health configure-openbot-hermes.py openbot-network.json openbot-install.json; do
  test -f "$file" && test ! -L "$file"
  cp -p -- "$file" "$stage/$file"
done
sed -i "s|^    image: .*|    image: $image|" "$stage/compose.yaml"
sed -i "s|^readonly IMAGE_REF=.*|readonly IMAGE_REF=$image|" "$stage/homecompute-openbot-health"
printf '%s\n' "$base" > "$stage/image-base-revision"
test "$(docker compose --file "$stage/compose.yaml" config --images)" = "$image"
docker pull --platform linux/amd64 "$image"
if [[ -e "$release" || -L "$release" ]]; then
  test -d "$release" && test ! -L "$release"
  diff -qr -- "$stage" "$release"
else
  mv -T -- "$stage" "$release"
fi
test "$(readlink -f "$root/current")" = "$old"
restore_before_start=true
sudo -n systemctl stop homecompute-openbot.service
ln -sfnT -- "$release" "$root/current"
sudo -n install -o root -g root -m 0755 "$release/homecompute-openbot-health" /usr/local/libexec/homecompute-openbot-health
# A new start may migrate /data. Recovery after that point uses the data backup.
restore_before_start=false
sudo -n systemctl start homecompute-openbot.service
sudo -n /usr/local/libexec/homecompute-openbot-health
docker exec homecompute-openbot openbot version
REMOTE_IMAGE
}

render_team_pin() {
  case "$1" in
    compose.yaml) sed "s|^    image: .*|    image: $2|" ;;
    homecompute-openbot-health) sed "s|^readonly IMAGE_REF=.*|readonly IMAGE_REF=$2|" ;;
    *) cat ;;
  esac
}

status() {
  # shellcheck disable=SC2016 # Expand the container name on the guest.
  ssh_guest 'sudo -n systemctl is-active homecompute-openbot-firewall.service homecompute-openbot.service || true; for c in homecompute-openbot homecompute-openbot-operator; do sudo -n docker inspect --format "{{.Name}} {{.State.Status}} {{.Config.Image}} ports={{json .HostConfig.PortBindings}}" "$c" 2>/dev/null || true; sudo -n docker exec "$c" openbot version 2>/dev/null || true; done'
}

login_server() {
  if [[ "${3:-full}" == scoped ]]; then
    require_scoped_release
  else
    require_published_release
  fi
  ssh -tt -J home-core -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -i "$KEY_FILE" \
    "$GUEST" "sudo -n docker exec -it $1 openbot login --name $2"
}

install_openbot() {
  local mode="${1:-full}"
  if [[ "$mode" == scoped ]]; then
    require_scoped_release
  else
    require_published_release
  fi
  local revision stage release compose_package compose_version compose_cli_version compose_sha256 compose_architecture
  revision="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  compose_package="$(jq -er '.docker_compose.apt_package' "$REPO_ROOT/config/openbot-install.json")"
  compose_version="$(jq -er '.docker_compose.apt_version' "$REPO_ROOT/config/openbot-install.json")"
  compose_cli_version="$(jq -er '.docker_compose.cli_version' "$REPO_ROOT/config/openbot-install.json")"
  compose_sha256="$(jq -er '.docker_compose.deb_sha256' "$REPO_ROOT/config/openbot-install.json")"
  compose_architecture="$(jq -er '.architecture' "$REPO_ROOT/config/openbot-install.json")"
  stage="/tmp/homecompute-openbot-$revision"
  release="$REMOTE_BASE/releases/$revision"
  ssh_guest bash -s -- "$REMOTE_BASE" "$stage" "$release" "$compose_package" \
     "$compose_version" "$compose_cli_version" "$compose_sha256" "$compose_architecture" "$mode" <<'REMOTE_PREP'
set -Eeuo pipefail
base="$1"; stage="$2"; compose_package="$4"; compose_version="$5"
compose_cli_version="$6"; compose_sha256="$7"; compose_architecture="$8"; mode="$9"
command -v docker >/dev/null
docker info >/dev/null
test -r /home/hermes-operator/.config/homecompute-hermes/certs/home-core-root.crt || {
  echo 'Home Core root CA is missing from the Hermes guest configuration' >&2; exit 1;
}
test ! -L "$base" || { echo 'OpenBot data root must not be a symlink' >&2; exit 1; }
current="$base/current"
if test -e "$current" || test -L "$current"; then
  test -L "$current" || { echo 'Existing OpenBot current path is not a symlink' >&2; exit 1; }
  previous_release="$(readlink -f "$current")"
  previous_revision="${previous_release##*/}"
  [[ "$previous_revision" =~ ^[0-9a-f]{40}$ &&
    ( "$previous_release" == "$base/releases/$previous_revision" ||
      "$previous_release" == "$base/releases/team-image/$previous_revision" ) ]] || {
    echo 'Existing OpenBot release is outside its managed version directory' >&2
    exit 1
  }
  # Keep the pre-existing release immutable. A newly added source-managed helper
  # need not exist in that older release, but an existing copy must be a real
  # file so a path substitution cannot be mistaken for managed source.
  for file in compose.yaml seccomp.json homecompute-openbot.service homecompute-openbot-firewall.service network-policy.py homecompute-openbot-health openbot-network.json openbot-install.json; do
    test -f "$previous_release/$file" && test ! -L "$previous_release/$file" || {
      echo 'Existing OpenBot release is incomplete; preserve and inspect it' >&2; exit 1;
    }
  done
  if test -e "$previous_release/configure-openbot-hermes.py" || test -L "$previous_release/configure-openbot-hermes.py"; then
    test -f "$previous_release/configure-openbot-hermes.py" && test ! -L "$previous_release/configure-openbot-hermes.py" || {
      echo 'Existing Hermes setup helper is not a regular managed file; preserve and inspect it' >&2; exit 1;
    }
  fi
  cmp -s "$previous_release/homecompute-openbot.service" /etc/systemd/system/homecompute-openbot.service || {
    echo 'Existing OpenBot systemd unit differs from its managed release' >&2; exit 1;
  }
  cmp -s "$previous_release/homecompute-openbot-firewall.service" /etc/systemd/system/homecompute-openbot-firewall.service || {
    echo 'Existing OpenBot firewall unit differs from its managed release' >&2; exit 1;
  }
  cmp -s "$previous_release/openbot-network.json" /etc/homecompute-openbot/network.json || {
    echo 'Existing OpenBot network configuration differs from its managed release' >&2; exit 1;
  }
  cmp -s "$previous_release/network-policy.py" /usr/local/libexec/homecompute-openbot-network-policy || {
    echo 'Existing OpenBot network helper differs from its managed release' >&2; exit 1;
  }
  cmp -s "$previous_release/homecompute-openbot-health" /usr/local/libexec/homecompute-openbot-health || {
    echo 'Existing OpenBot health helper differs from its managed release' >&2; exit 1;
  }
  if docker inspect homecompute-openbot >/dev/null 2>&1; then
    project="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' homecompute-openbot)"
    service="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.service" }}' homecompute-openbot)"
    [[ "$project" == homecompute-openbot && "$service" == openbot ]] || {
      echo 'Existing container name is not owned by the OpenBot Compose project' >&2; exit 1;
    }
  fi
  test ! -L "$base/data" && test "$(stat -c %a "$base/data")" = 700 && test "$(stat -c %u "$base/data")" = 1000 || {
    echo 'Existing OpenBot data directory has unexpected ownership or permissions' >&2; exit 1;
  }
  if test -e "$base/operator-data" || test -L "$base/operator-data"; then
    test ! -L "$base/operator-data" && test -d "$base/operator-data" \
      && test "$(stat -c %a "$base/operator-data")" = 700 \
      && test "$(stat -c %u "$base/operator-data")" = 1000 || {
      echo 'Existing OpenBot operator data directory has unexpected ownership or permissions' >&2; exit 1;
    }
  fi
  if docker inspect homecompute-openbot-operator >/dev/null 2>&1; then
    project="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}' homecompute-openbot-operator)"
    service="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.service" }}' homecompute-openbot-operator)"
    [[ "$project" == homecompute-openbot && "$service" == openbot_operator ]] || {
      echo 'Existing operator container name is not owned by the OpenBot Compose project' >&2; exit 1;
    }
  fi
else
  for path in "$base/compose.yaml" "$base/seccomp.json" \
      /etc/homecompute-openbot/network.json \
      /etc/systemd/system/homecompute-openbot.service \
      /etc/systemd/system/homecompute-openbot-firewall.service \
      /usr/local/libexec/homecompute-openbot-network-policy \
      /usr/local/libexec/homecompute-openbot-health; do
    if test -e "$path" || test -L "$path"; then
      echo 'Unmanaged OpenBot path already exists; preserve and inspect it' >&2
      exit 1
    fi
  done
  if docker inspect homecompute-openbot >/dev/null 2>&1; then
    echo 'Container name already exists; preserve and inspect it' >&2
    exit 1
  fi
  if docker inspect homecompute-openbot-operator >/dev/null 2>&1 || test -e "$base/operator-data" || test -L "$base/operator-data"; then
    echo 'Unmanaged OpenBot operator state already exists; preserve and inspect it' >&2
    exit 1
  fi
fi
for path in "$base/compose.yaml" "$base/seccomp.json"; do
  if test -e "$path" || test -L "$path"; then
    echo 'Unmanaged OpenBot path already exists; preserve and inspect it' >&2
    exit 1
  fi
done
command -v apt-get >/dev/null
test "$(dpkg --print-architecture)" = "$compose_architecture"
installed_compose_version="$(dpkg-query -W -f='${Version}' "$compose_package" 2>/dev/null || true)"
if test "$installed_compose_version" != "$compose_version"; then
  [[ "$mode" != scoped ]] || { echo 'Scoped install requires the existing pinned Compose package.' >&2; exit 1; }
  sudo -n apt-get update -qq
  package_dir="$(mktemp -d /tmp/homecompute-openbot-compose.XXXXXX)"
  trap 'rm -rf "$package_dir"' EXIT
  (cd "$package_dir" && apt-get download "$compose_package=$compose_version")
  package="$package_dir/${compose_package}_${compose_version}_${compose_architecture}.deb"
  test -f "$package"
  printf '%s  %s\n' "$compose_sha256" "$package" | sha256sum -c -
  sudo -n env DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends "$package"
fi
test "$(dpkg-query -W -f='${Version}' "$compose_package")" = "$compose_version"
reported_compose_version="$(docker compose version --short | sed 's/^v//')"
case "$reported_compose_version" in
  "$compose_cli_version"|"$compose_cli_version"+*) ;;
  *) echo 'Installed Compose CLI version differs from the pinned source version' >&2; exit 1 ;;
esac
install -d -m 0700 "$base" "$base/releases" "$stage"
REMOTE_PREP

  scp -q -J home-core -o BatchMode=yes -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -i "$KEY_FILE" "${ASSETS[@]}" "$GUEST:$stage/"

  ssh_guest bash -s -- "$REMOTE_BASE" "$stage" "$release" "$mode" <<'REMOTE_INSTALL'
set -Eeuo pipefail
base="$1"; stage="$2"; release="$3"; mode="$4"
root_ca=/home/hermes-operator/.config/homecompute-hermes/certs/home-core-root.crt
bundle="$stage/agent-ca-bundle.pem"
# Keep public roots for hosted services and add the private gateway root for agents.
cat /etc/ssl/certs/ca-certificates.crt "$root_ca" > "$bundle"
openssl verify -CAfile "$bundle" "$root_ca" >/dev/null
if [[ "$mode" == scoped ]]; then
  exec 9>"$base/image-upgrade.lock"
  flock -n 9
  previous="$(readlink -f "$base/current")"
  [[ "$previous" == "$base/releases/"* && "${previous##*/}" =~ ^[0-9a-f]{40}$ ]] || exit 1
  expected_image="$(awk '$1 == "image:" && !found++ { print $2 }' "$stage/compose.yaml")"
  [[ "$expected_image" =~ ^ghcr[.]io/nightly-labs/openbot@sha256:[0-9a-f]{64}$ ]] || exit 1
  [[ "$expected_image" == "$(sudo -n docker inspect --format '{{.Config.Image}}' homecompute-openbot)" ]] || {
    echo 'Scoped install preserves the team image.' >&2; exit 1;
  }
  sudo -n systemctl --quiet is-active homecompute-openbot.service homecompute-openbot-firewall.service
  if [[ "$previous" == "$release" ]]; then
    sudo -n /usr/local/libexec/homecompute-openbot-health
    exit 0
  fi
  cmp -s "$root_ca" /etc/homecompute-openbot/home-core-root.crt
  cmp -s "$bundle" /etc/homecompute-openbot/agent-ca-bundle.pem
  rollback_scoped() {
    trap - EXIT
    trap '' HUP INT TERM
    set +e
    systemctl_cmd=(sudo -n systemctl)
    "${systemctl_cmd[@]}" stop homecompute-openbot.service
    "${systemctl_cmd[@]}" stop homecompute-openbot-firewall.service
    sudo -n docker stop homecompute-openbot-operator >/dev/null 2>&1 || true
    ln -sfnT "$previous" "$base/current"
    for unit in homecompute-openbot.service homecompute-openbot-firewall.service; do
      sudo -n install -o root -g root -m 0644 "$previous/$unit" "/etc/systemd/system/$unit"
    done
    sudo -n install -o root -g root -m 0644 "$previous/openbot-network.json" /etc/homecompute-openbot/network.json
    sudo -n install -o root -g root -m 0755 "$previous/network-policy.py" /usr/local/libexec/homecompute-openbot-network-policy
    sudo -n install -o root -g root -m 0755 "$previous/homecompute-openbot-health" /usr/local/libexec/homecompute-openbot-health
    "${systemctl_cmd[@]}" daemon-reload
    "${systemctl_cmd[@]}" start homecompute-openbot-firewall.service homecompute-openbot.service
    "${systemctl_cmd[@]}" --quiet is-active homecompute-openbot-firewall.service homecompute-openbot.service &&
      sudo -n /usr/local/libexec/homecompute-openbot-health || {
      echo 'Previous OpenBot guest release requires recovery.' >&2; exit 1;
    }
    echo 'Previous OpenBot guest release restored; preserve operator data for review.' >&2
    exit 1
  }
  trap rollback_scoped EXIT
  trap 'exit 1' HUP INT TERM
  sudo -n systemctl stop homecompute-openbot.service
else
  sudo -n systemctl stop homecompute-openbot.service >/dev/null 2>&1 || true
fi
install -d -o hermes-operator -g hermes-operator -m 0700 "$release"
for file in compose.yaml seccomp.json homecompute-openbot.service homecompute-openbot-firewall.service network-policy.py homecompute-openbot-health configure-openbot-hermes.py openbot-network.json openbot-install.json; do
  if test -e "$release/$file" || test -L "$release/$file"; then
    cmp -s "$stage/$file" "$release/$file" || { echo 'Published release assets differ; refusing overwrite' >&2; exit 1; }
  else
    sudo -n install -o hermes-operator -g hermes-operator -m 0600 "$stage/$file" "$release/$file"
  fi
done
sudo -n chmod 0644 "$release/compose.yaml" "$release/seccomp.json" "$release/homecompute-openbot.service" "$release/homecompute-openbot-firewall.service" "$release/openbot-network.json" "$release/openbot-install.json"
sudo -n chmod 0755 "$release/network-policy.py" "$release/homecompute-openbot-health" "$release/configure-openbot-hermes.py"
sudo -n install -d -o root -g root -m 0755 /etc/homecompute-openbot /usr/local/libexec
if [[ "$mode" != scoped ]]; then
  sudo -n install -o root -g root -m 0444 "$root_ca" /etc/homecompute-openbot/home-core-root.crt
  sudo -n install -o root -g root -m 0444 "$bundle" /etc/homecompute-openbot/agent-ca-bundle.pem
fi
sudo -n install -o root -g root -m 0644 "$release/openbot-network.json" /etc/homecompute-openbot/network.json
sudo -n install -o root -g root -m 0755 "$release/network-policy.py" /usr/local/libexec/homecompute-openbot-network-policy
sudo -n install -o root -g root -m 0755 "$release/homecompute-openbot-health" /usr/local/libexec/homecompute-openbot-health
for file in homecompute-openbot.service homecompute-openbot-firewall.service; do
  sudo -n install -o root -g root -m 0644 "$release/$file" "/etc/systemd/system/$file"
done
if test ! -e "$base/data" && test ! -L "$base/data"; then
  install -d -o hermes-operator -g hermes-operator -m 0700 "$base/data"
fi
test ! -L "$base/data"
test "$(stat -c %a "$base/data")" = 700
test "$(stat -c %u "$base/data")" = 1000
if test ! -e "$base/operator-data" && test ! -L "$base/operator-data"; then
  install -d -o hermes-operator -g hermes-operator -m 0700 "$base/operator-data"
fi
test ! -L "$base/operator-data"
test "$(stat -c %a "$base/operator-data")" = 700
test "$(stat -c %u "$base/operator-data")" = 1000
ln -sfnT "$release" "$base/current"
sudo -n systemctl daemon-reload
sudo -n systemctl enable homecompute-openbot-firewall.service
sudo -n systemctl restart homecompute-openbot-firewall.service
sudo -n systemctl enable --now homecompute-openbot.service
sudo -n systemctl --no-pager --quiet is-active homecompute-openbot.service
sudo -n /usr/local/libexec/homecompute-openbot-health
trap - EXIT HUP INT TERM
rm -rf "$stage"
echo 'OpenBot installed and digest-pinned. Account sign-in and model-provider setup remain pending.'
REMOTE_INSTALL
}

main() {
  [[ $# == 1 ]] || { usage >&2; return 2; }
  case "$1" in
    install) install_openbot ;;
    install-scoped) install_openbot scoped ;;
    verify-scoped) require_scoped_release ;;
    upgrade-team-image) upgrade_team_image ;;
    login) login_server homecompute-openbot home-core ;;
    login-operator) login_server homecompute-openbot-operator home-core-operator ;;
    login-operator-scoped) login_server homecompute-openbot-operator home-core-operator scoped ;;
    status) status ;;
    configure-compression) configure_compression ;;
    -h|--help|help) usage ;;
    *) usage >&2; return 2 ;;
  esac
}

main "$@"
