#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd "$(dirname "$0")/.." && pwd)"
pin_file="$repo_root/config/craft-anything-toolbox.json"
version="$(jq -er '.craft_anything.version' "$pin_file")"
url="$(jq -er '.craft_anything.linux_x86_64_archive' "$pin_file")"
expected="$(jq -er '.craft_anything.sha256' "$pin_file")"
work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT

case "$url" in
  "https://github.com/storytold/craft-anything/releases/download/v$version/craft-anything-$version-linux-x86_64.tar.gz") ;;
  *) echo "unexpected Craft Anything release URL" >&2; exit 1 ;;
esac

curl --fail --location --proto '=https' --tlsv1.2 --output "$work/craft.tar.gz" "$url"
printf '%s  %s\n' "$expected" "$work/craft.tar.gz" | sha256sum --check --status
tar -tzf "$work/craft.tar.gz" > "$work/members"
mapfile -t binaries < <(grep -E '(^|/)craft-anything-cli$' "$work/members")
if [[ "${#binaries[@]}" -ne 1 ]]; then
  echo "release archive must contain exactly one craft-anything-cli executable" >&2
  exit 1
fi
tar -xzf "$work/craft.tar.gz" -C "$work" "${binaries[0]}"
install -D -m 0755 "$work/${binaries[0]}" /usr/local/bin/craft-anything-cli
echo "Installed Craft Anything $version (SHA-256 verified)."
