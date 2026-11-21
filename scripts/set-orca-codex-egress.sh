#!/usr/bin/env bash
# Preserve the original Codex network selection command.
exec bash "$(dirname -- "${BASH_SOURCE[0]}")/set-orca-cloud-egress.sh" codex "$@"
