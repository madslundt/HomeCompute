#!/bin/bash
set -euo pipefail

read_required_secret() {
  local secret_path="$1"
  if [[ ! -r "$secret_path" ]]; then
    echo "required secret is not readable: $secret_path" >&2
    exit 1
  fi
  local value
  value="$(<"$secret_path")"
  if [[ -z "$value" ]]; then
    echo "required secret is empty: $secret_path" >&2
    exit 1
  fi
  printf '%s' "$value"
}

export OPENAI_API_KEYS="$(read_required_secret /run/secrets/litellm_api_key)"
export WEBUI_SECRET_KEY="$(read_required_secret /run/secrets/webui_secret_key)"

if (( ${#WEBUI_SECRET_KEY} < 32 )); then
  echo "WEBUI_SECRET_KEY must contain at least 32 characters" >&2
  exit 1
fi

exec "$@"
