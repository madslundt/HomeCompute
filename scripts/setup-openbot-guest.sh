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
Usage: setup-openbot-guest.sh install|retire-operator|purge-retired-operator|plan-team-image|upgrade-team-image|login|status|configure-compression|configure-model-gateway|personal-models

install  Install or reconcile the pinned service in the agents VM. The current
         checkout must be a clean commit equal to origin/master and Home Core's
         deployed revision. Managed upgrades preserve /data and refuse drift.
         This does not sign in to OpenBot or configure a provider.
retire-operator  Switch to the team-only release with a verified VM backup.
purge-retired-operator  Delete the retired instance’s local state after verification.
plan-team-image  Show the deployed and published image digests for review.
upgrade-team-image  Apply the published image pin in a guest systemd job.
status   Read guest systemd and container status without showing account data.
login    Complete the official email-code server sign-in in this terminal.
configure-compression  Apply only the Hermes threshold from current published master.
configure-model-gateway  Verify keyless HTTPS and migrate the embedded Hermes model settings.
personal-models [plan|apply|rollback]  Reconcile the three owner interactive model choices.
install-partner          Install the independent household server from published master.
status-partner           Show the household server state and installed revision.
login-partner            Enroll the household server using its owner's email code.
configure-partner-hermes Install and configure the household server's embedded Hermes for local inference.
install-partner-profile FILE  Stream the owner's reviewed USER.md into private Hermes memory.
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

require_fresh_vm_backup() {
  require_current_master_checkout
  local revision release
  revision="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  release="/srv/homecompute/releases/$revision"
  ssh -o BatchMode=yes -o StrictHostKeyChecking=yes home-core \
    "sudo -n python3 $release/scripts/backup-openbot-agents-vm.py verify --revision $revision"
}

configure_compression() {
  require_current_master_checkout
  local revision
  revision="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  git -C "$REPO_ROOT" show "$revision:scripts/configure-openbot-hermes.py" |
    ssh_guest python3 - --compression-only
}

configure_model_gateway() {
  require_published_release
  local revision
  revision="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  git -C "$REPO_ROOT" show "$revision:scripts/configure-openbot-hermes.py" |
    ssh_guest python3 -
}

personal_models() {
  local mode=${1:-plan} revision
  case "$mode" in
    plan) require_published_release ;;
    apply|rollback)
      require_published_release
      revision="$(git -C "$REPO_ROOT" rev-parse HEAD)"
      ssh -o BatchMode=yes -o StrictHostKeyChecking=yes home-core \
        "sudo -n python3 /srv/homecompute/releases/$revision/scripts/backup-openbot-agents-vm.py verify-owner --revision $revision" ||
        require_fresh_vm_backup
      ;;
    *) echo 'Use personal-models plan, apply or rollback.' >&2; return 1 ;;
  esac
  revision="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  # shellcheck disable=SC2016 # Expand the private staging path on the guest.
  git -C "$REPO_ROOT" archive "$revision" config/openbot-personal-team.json scripts/configure-openbot-personal-models.py |
    ssh_guest 'set -eu
      umask 077
      stage="$(mktemp -d /tmp/homecompute-openbot-personal.XXXXXXXX)"
      trap '\''rm -rf -- "$stage"'\'' EXIT
      tar -xf - -C "$stage"
      docker exec -i homecompute-openbot python3 -c "$(cat "$stage/scripts/configure-openbot-personal-models.py")" \
        /dev/stdin '"$mode"' < "$stage/config/openbot-personal-team.json"'
}

install_partner() {
  require_fresh_vm_backup
  local revision
  revision="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  # shellcheck disable=SC2016 # The staging directory is expanded on the guest.
  git -C "$REPO_ROOT" archive "$revision" deploy/openbot config/openbot-network.json scripts/configure-openbot-hermes.py |
    ssh_guest 'set -eu
      stage="$(mktemp -d /tmp/homecompute-openbot-partner.XXXXXXXX)"
      trap '\''rm -rf -- "$stage"'\'' EXIT
      tar -xf - -C "$stage"
      sudo -n /bin/bash "$stage/deploy/openbot/install-partner.sh" "$stage" '"$revision"
}

status_partner() {
  ssh_guest 'readlink /etc/homecompute-openbot-partner/current || true
    systemctl is-active homecompute-openbot-partner.service homecompute-openbot-partner-firewall.service || true
    docker inspect --format "{{.Name}} {{.State.Status}} {{.Config.Image}} ports={{json .HostConfig.PortBindings}}" homecompute-openbot-partner'
}

login_partner() {
  require_current_master_checkout
  ssh -tt -J home-core -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -i "$KEY_FILE" \
    "$GUEST" 'sudo -n docker exec -it homecompute-openbot-partner openbot login --name home-core-partner'
}

configure_partner_hermes() {
  require_current_master_checkout
  ssh_guest docker exec -i homecompute-openbot-partner /bin/bash -s \
    < "$REPO_ROOT/deploy/openbot/install-partner-hermes.sh"
  ssh_guest /usr/bin/python3 /etc/homecompute-openbot-partner/current/configure-openbot-hermes.py \
    --container homecompute-openbot-partner "$@"
}

image_pin() {
  git -C "$REPO_ROOT" show "$1:deploy/openbot/compose.yaml" |
    awk '$1 == "image:" && !found++ { print $2 }'
}

plan_team_image() {
  require_current_master_checkout
  local revision image running
  revision="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  image="$(image_pin "$revision")"
  running="$(ssh_guest 'docker inspect --format "{{.Config.Image}}" homecompute-openbot')"
  local pin_pattern='^ghcr[.]io/nightly-labs/openbot@sha256:[0-9a-f]{64}$'
  [[ "$running" =~ $pin_pattern && "$image" =~ $pin_pattern ]]
  printf 'Revision: %s\nRunning: %s\nPublished: %s\n' "$revision" "$running" "$image"
}

upgrade_team_image() {
  require_fresh_vm_backup
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
  local job_script
  job_script="$(ssh_guest 'umask 077; mktemp /tmp/homecompute-openbot-image.XXXXXXXX')"
  [[ "$job_script" =~ ^/tmp/homecompute-openbot-image[.][a-zA-Z0-9]{8}$ ]]
  ssh_guest "cat > $job_script" <<'REMOTE_IMAGE'
set -Eeuo pipefail
umask 077
rm -f -- "$0"
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
  local unit="homecompute-openbot-image-$revision"
  printf 'Guest upgrade unit: %s.service\n' "$unit"
  ssh_guest sudo -n systemd-run --wait --collect --uid=hermes-operator \
    --property=RuntimeMaxSec=20min --property=StandardOutput=journal \
    --property=StandardError=journal --unit="$unit" \
    /usr/bin/bash "$job_script" "$REMOTE_BASE" "$previous" "$base" "$revision" \
    "$old_image" "$new_image" "$manifest"
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
  require_published_release
  ssh -tt -J home-core -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes -i "$KEY_FILE" \
    "$GUEST" "sudo -n docker exec -it $1 openbot login --name $2"
}

install_openbot() {
  local mode="${1:-full}"
  if [[ "$mode" == retire ]]; then
    require_fresh_vm_backup
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
    [[ "$mode" == retire ]] || { echo 'Use retire-operator for an existing operator.' >&2; exit 1; }
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
  [[ "$mode" != retire ]] || { echo 'Operator retirement preserves the installed Compose package.' >&2; exit 1; }
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
if [[ "$mode" == retire ]]; then
  exec 9>"$base/image-upgrade.lock"
  flock -n 9
  previous="$(readlink -f "$base/current")"
  [[ "$previous" == "$base/releases/"* && "${previous##*/}" =~ ^[0-9a-f]{40}$ ]] || exit 1
  expected_image="$(awk '$1 == "image:" && !found++ { print $2 }' "$stage/compose.yaml")"
  [[ "$expected_image" =~ ^ghcr[.]io/nightly-labs/openbot@sha256:[0-9a-f]{64}$ ]] || exit 1
  [[ "$expected_image" == "$(sudo -n docker inspect --format '{{.Config.Image}}' homecompute-openbot)" ]] || {
    echo 'Operator retirement preserves the team image.' >&2; exit 1;
  }
  sudo -n systemctl --quiet is-active homecompute-openbot.service
  sudo -n systemctl --quiet is-active homecompute-openbot-firewall.service
  if [[ "$previous" == "$release" ]]; then
    sudo -n /usr/local/libexec/homecompute-openbot-health
    rm -rf -- "$stage"
    exit 0
  fi
  cmp -s "$root_ca" /etc/homecompute-openbot/home-core-root.crt
  cmp -s "$bundle" /etc/homecompute-openbot/agent-ca-bundle.pem
  restore_previous_release() {
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
    "${systemctl_cmd[@]}" --quiet is-active homecompute-openbot-firewall.service &&
      "${systemctl_cmd[@]}" --quiet is-active homecompute-openbot.service &&
      sudo -n /usr/local/libexec/homecompute-openbot-health || {
      echo 'Previous OpenBot guest release requires recovery.' >&2; exit 1;
    }
    echo 'Previous OpenBot guest release restored; preserve operator data for review.' >&2
    exit 1
  }
  trap restore_previous_release EXIT
  trap 'exit 1' HUP INT TERM
  # Stop the old unit before replacing its firewall helper. Its ExecStop
  # removes both the original team chain and the operator chain.
  sudo -n systemctl stop homecompute-openbot.service
  sudo -n systemctl stop homecompute-openbot-firewall.service
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
if [[ "$mode" != retire ]]; then
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
ln -sfnT "$release" "$base/current"
sudo -n systemctl daemon-reload
sudo -n systemctl enable homecompute-openbot-firewall.service
sudo -n systemctl restart homecompute-openbot-firewall.service
sudo -n systemctl enable --now homecompute-openbot.service
sudo -n systemctl --no-pager --quiet is-active homecompute-openbot.service
sudo -n /usr/local/libexec/homecompute-openbot-health
trap - EXIT HUP INT TERM
rm -rf "$stage"
echo 'OpenBot team server is running the published image.'
REMOTE_INSTALL
}

purge_retired_operator() {
  require_fresh_vm_backup
  local revision
  revision="$(git -C "$REPO_ROOT" rev-parse HEAD)"
  ssh_guest bash -s -- "$REMOTE_BASE" "$revision" <<'REMOTE_PURGE'
set -Eeuo pipefail
base="$1"; revision="$2"
exec 9>"$base/image-upgrade.lock"
flock -n 9
release="$base/releases/$revision"
test "$(readlink -f "$base/current")" = "$release"
cd "$release"
test "$(docker compose --file compose.yaml config --services)" = openbot
sudo -n systemctl --quiet is-active homecompute-openbot.service
sudo -n systemctl --quiet is-active homecompute-openbot-firewall.service
sudo -n /usr/local/libexec/homecompute-openbot-health
mount_source="$(docker inspect --format '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' homecompute-openbot)"
test "$mount_source" = "$base/data"
test -d "$base/data" && test ! -L "$base/data"
team_inode="$(stat -c '%d:%i' "$base/data")"
# Revoke the operator’s old gateway key before deleting its only local copy.
test ! -e "$HOME/.config/homecompute-openbot-operator/secrets/litellm-api-key"
operator=homecompute-openbot-operator
if docker inspect "$operator" >/dev/null 2>&1; then
  test "$(docker inspect --format '{{.State.Running}} {{index .Config.Labels "com.docker.compose.project"}} {{index .Config.Labels "com.docker.compose.service"}}' "$operator")" = 'false homecompute-openbot openbot_operator'
  docker rm "$operator" >/dev/null
fi
if docker network inspect "$operator" >/dev/null 2>&1; then
  test "$(docker network inspect --format '{{index .Labels "com.docker.compose.project"}} {{index .Labels "com.docker.compose.network"}} {{len .Containers}}' "$operator")" = 'homecompute-openbot openbot_operator 0'
  docker network rm "$operator" >/dev/null
fi
# Delete only the retired instance’s local state. Never follow substituted
# paths or remove a directory that is a mount point or aliases team state.
for path in "$base/operator-data" "$HOME/.config/homecompute-openbot-operator"; do
  test ! -L "$path"
  if test -e "$path"; then
    test -d "$path" && test "$(realpath -e "$path")" = "$path"
    ! mountpoint -q "$path"
    test "$(stat -c '%d:%i' "$path")" != "$team_inode"
  fi
done
sudo -n rm -rf --one-file-system -- "$base/operator-data" "$HOME/.config/homecompute-openbot-operator"
test ! -e "$base/operator-data" && test ! -e "$HOME/.config/homecompute-openbot-operator"
test "$(stat -c '%d:%i' "$base/data")" = "$team_inode"
sudo -n /usr/local/libexec/homecompute-openbot-health
printf '%s\n' 'Removed the retired OpenBot instance and verified the team data mount.'
REMOTE_PURGE
}

main() {
  if [[ "${1:-}" == personal-models && $# -le 2 ]]; then
    shift
    personal_models "$@"
    return
  fi
  if [[ $# == 2 && "$1" == install-partner-profile ]]; then
    [[ -f "$2" && ! -L "$2" ]] || { echo 'Supply a reviewed local profile file.' >&2; return 2; }
    configure_partner_hermes --install-user-profile < "$2"
    return
  fi
  [[ $# == 1 ]] || { usage >&2; return 2; }
  case "$1" in
    install-partner) install_partner ;;
    status-partner) status_partner ;;
    login-partner) login_partner ;;
    configure-partner-hermes) configure_partner_hermes ;;
    install) install_openbot ;;
    retire-operator) install_openbot retire ;;
    purge-retired-operator) purge_retired_operator ;;
    plan-team-image) plan_team_image ;;
    upgrade-team-image) upgrade_team_image ;;
    login) login_server homecompute-openbot home-core ;;
    status) status ;;
    configure-compression) configure_compression ;;
    configure-model-gateway) configure_model_gateway ;;
    -h|--help|help) usage ;;
    *) usage >&2; return 2 ;;
  esac
}

main "$@"
