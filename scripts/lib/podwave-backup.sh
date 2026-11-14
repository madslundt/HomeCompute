#!/usr/bin/env bash

# Stop an existing healthy PodWave container for a consistent root-only
# snapshot. Arguments are the release revision followed by its Compose argv.
# Returns the archive path on stdout, or an empty string for first install.
backup_podwave_state() {
  local revision="$1"
  shift
  local -a compose=("$@")
  local container backup_dir backup_path

  container="$("${compose[@]}" ps -q podwave)"
  if [[ -z "$container" ]]; then
    printf ''
    return 0
  fi
  [[ $(docker inspect --format '{{.State.Health.Status}}' "$container") == healthy ]] || {
    printf 'The existing PodWave container is not healthy; preserving it for recovery.\n' >&2
    return 1
  }
  [[ -d /srv/state/podwave && ! -L /srv/state/podwave && $(stat -c '%u' /srv/state/podwave) == 1000 ]] || {
    printf 'PodWave state must remain a real directory owned by UID 1000.\n' >&2
    return 1
  }
  backup_dir=/srv/state/control-plane/backups/podwave
  install -d -m 0700 "$backup_dir"
  [[ $(stat -c '%u:%a' "$backup_dir") == 0:700 ]] || {
    printf 'PodWave backup directory must be root-only.\n' >&2
    return 1
  }
  backup_path="$backup_dir/podwave-before-$revision-$(date -u +%Y%m%dT%H%M%S%NZ).tar.gz"

  if ! "${compose[@]}" stop podwave >/dev/null; then
    "${compose[@]}" start podwave >/dev/null 2>&1 ||
      printf 'PodWave stop failed and the previous service could not be restarted; inspect it before retrying.\n' >&2
    return 1
  fi
  if ! {
    tar --numeric-owner --xattrs --acls --gzip --create --file "$backup_path" -C /srv/state podwave &&
      chmod 0600 "$backup_path" &&
      tar --gzip --list --file "$backup_path" >/dev/null &&
      sha256sum "$backup_path" > "$backup_path.sha256" &&
      chmod 0600 "$backup_path.sha256" &&
      sha256sum --check --status "$backup_path.sha256"
  }; then
    "${compose[@]}" start podwave >/dev/null 2>&1 ||
      printf 'PodWave backup failed and the previous service could not be restarted; inspect it before retrying.\n' >&2
    printf 'PodWave state backup failed; the existing workload was preserved where possible.\n' >&2
    return 1
  fi
  printf '%s\n' "$backup_path"
}
