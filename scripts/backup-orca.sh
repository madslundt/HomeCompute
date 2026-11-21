#!/usr/bin/env bash
# Called under the shared deployment lock by the operator, backup timer and release wrapper.
set -Eeuo pipefail

if [[ ${1:-} != --deployment-lock-held ]]; then
  [[ $# == 0 ]] || exit 2
  exec 9>/var/lib/homecompute/deploy.lock
  flock -n 9 || exit 75
else
  shift
fi
[[ $# == 0 && $EUID == 0 && $(< /proc/sys/kernel/hostname) == home-core ]] || exit 2

container=homecompute-orca-orca-1
state=/srv/state/orca
archives=/srv/backups/agent-recovery/orca
[[ -d "$state" && ! -L "$state" && $(stat -c %u:%g:%a "$state") == 12074:12074:700 ]] || exit 1
known=$(docker ps --all --filter "name=^/${container}$" --format '{{.Names}}')
if [[ -z "$known" ]]; then
  printf 'Orca has no installed container; skipping backup.\n'
  exit 0
fi
[[ "$known" == "$container" ]]
metadata=$(docker inspect --type container --format '{{.State.Running}} {{index .Config.Labels "com.docker.compose.project"}} {{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}} {{.RW}}{{end}}{{end}}' "$container")
case "$metadata" in
  "true homecompute-orca $state true") running=true ;;
  "false homecompute-orca $state true") running=false ;;
  *) printf 'Orca container mount verification failed.\n' >&2; exit 1 ;;
esac

umask 077
install -d -m 0700 "$archives"
[[ ! -L "$archives" && $(stat -c %u:%a "$archives") == 0:700 ]] || exit 1
partial=$(mktemp "$archives/.orca-XXXXXXXX.partial")
archive="$archives/orca-$(date -u +%Y%m%dT%H%M%S%NZ).tar.gz"
resume=false
cleanup() {
  result=$?
  trap - EXIT
  rm -f -- "$partial"
  if [[ $resume == true ]]; then
    docker start "$container" >/dev/null || result=1
  fi
  exit "$result"
}
trap cleanup EXIT
trap 'exit 1' INT TERM HUP

if [[ $running == true ]]; then
  resume=true
  docker stop --time 45 "$container" >/dev/null
fi
[[ $(docker inspect --format '{{.State.Running}}' "$container") == false ]]
tar --numeric-owner --gzip -cf "$partial" --exclude='*.sock' --exclude='./.cache' -C "$state" .
tar -tzf "$partial" >/dev/null
mv -nT -- "$partial" "$archive"
[[ ! -e "$partial" && -s "$archive" ]]
(cd "$archives" && sha256sum -- "$(basename "$archive")" > "$(basename "$archive").sha256")
sync -f "$archives"
printf 'Orca recovery archive recorded at %s\n' "$archive"
# Retain two complete daily/upgrade archives locally. Restic owns off-host retention.
find "$archives" -maxdepth 1 -type f -name 'orca-*.tar.gz' -printf '%f\n' |
  sort -r | tail -n +3 |
  while IFS= read -r old; do
    [[ "$old" =~ ^orca-[0-9]{8}T[0-9]{15}Z\.tar\.gz$ ]] || exit 1
    rm -f -- "$archives/$old" "$archives/$old.sha256"
  done
