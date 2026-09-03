#!/usr/bin/env bash

# Strictly load a small KEY=VALUE configuration file without evaluating shell
# syntax. Callers provide the complete key allow-list and the required owner.

config_stat_uid() {
  stat -c '%u' "$1" 2>/dev/null || stat -f '%u' "$1"
}

config_stat_mode() {
  stat -c '%a' "$1" 2>/dev/null || stat -f '%Lp' "$1"
}

config_path_is_trusted() {
  local path="$1"
  local expected_uid="$2"
  local label="$3"
  local mode
  local mode_decimal
  local parent

  [[ "$path" == /* ]] || {
    printf '%s must be an absolute path: %s\n' "$label" "$path" >&2
    return 1
  }
  [[ -f "$path" && ! -L "$path" ]] || {
    printf '%s must be a regular, non-symlink file: %s\n' "$label" "$path" >&2
    return 1
  }
  [[ "$(config_stat_uid "$path")" == "$expected_uid" ]] || {
    printf '%s must be owned by uid %s: %s\n' "$label" "$expected_uid" "$path" >&2
    return 1
  }

  mode="$(config_stat_mode "$path")"
  mode_decimal=$((8#$mode))
  (( (mode_decimal & 8#022) == 0 )) || {
    printf '%s must not be group- or world-writable (found %s): %s\n' "$label" "$mode" "$path" >&2
    return 1
  }

  parent="$(dirname -- "$path")"
  [[ -d "$parent" && ! -L "$parent" ]] || {
    printf '%s parent must be a non-symlink directory: %s\n' "$label" "$parent" >&2
    return 1
  }
  [[ "$(config_stat_uid "$parent")" == "$expected_uid" ]] || {
    printf '%s parent must be owned by uid %s: %s\n' "$label" "$expected_uid" "$parent" >&2
    return 1
  }
  mode="$(config_stat_mode "$parent")"
  mode_decimal=$((8#$mode))
  (( (mode_decimal & 8#022) == 0 )) || {
    printf '%s parent must not be group- or world-writable (found %s): %s\n' "$label" "$mode" "$parent" >&2
    return 1
  }
}

config_key_is_allowed() {
  local candidate="$1"
  shift
  local allowed

  for allowed in "$@"; do
    [[ "$candidate" == "$allowed" ]] && return 0
  done
  return 1
}

load_trusted_env_file() {
  local path="$1"
  local expected_uid="$2"
  local label="$3"
  shift 3
  local -a allowed_keys=("$@")
  local line
  local key
  local value
  local allowed
  local seen=$'\n'
  local line_number=0
  local -a parsed_keys=()
  local -a parsed_values=()

  config_path_is_trusted "$path" "$expected_uid" "$label" || return 1

  while IFS= read -r line || [[ -n "$line" ]]; do
    line_number=$((line_number + 1))
    line="${line%$'\r'}"
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [[ -z "$line" || "$line" == \#* ]] && continue

    if [[ "$line" =~ ^([A-Z][A-Z0-9_]*)=(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      value="${BASH_REMATCH[2]}"
    else
      printf '%s contains invalid syntax on line %s; expected KEY=VALUE\n' "$label" "$line_number" >&2
      return 1
    fi

    config_key_is_allowed "$key" "${allowed_keys[@]}" || {
      printf '%s contains unknown key on line %s: %s\n' "$label" "$line_number" "$key" >&2
      return 1
    }
    [[ "$seen" != *$'\n'"$key"$'\n'* ]] || {
      printf '%s contains duplicate key on line %s: %s\n' "$label" "$line_number" "$key" >&2
      return 1
    }

    if [[ "$value" == \"*\" && ${#value} -ge 2 ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "$value" == \'*\' && ${#value} -ge 2 ]]; then
      value="${value:1:${#value}-2}"
    elif [[ "$value" == \"* || "$value" == *\" || "$value" == \'* || "$value" == *\' ]]; then
      printf '%s contains unbalanced quotes on line %s\n' "$label" "$line_number" >&2
      return 1
    fi

    parsed_keys+=("$key")
    parsed_values+=("$value")
    seen+="$key"$'\n'
  done <"$path"

  # Commit only after the whole file has passed. Unsetting the allow-list first
  # prevents a removed key from inheriting a stale value from a prior load.
  for allowed in "${allowed_keys[@]}"; do
    unset "$allowed"
  done
  for ((line_number = 0; line_number < ${#parsed_keys[@]}; line_number++)); do
    export "${parsed_keys[$line_number]}=${parsed_values[$line_number]}"
  done
}
