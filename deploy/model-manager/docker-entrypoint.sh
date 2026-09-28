#!/bin/sh
set -eu

if [ "${MODEL_MANAGER_SPARK_FORCED_COMMAND_READY:-0}" != "1" ]; then
  echo "Spark forced-command readiness gate is not enabled" >&2
  exit 1
fi

install -d -m 0700 /run/model-manager
install -m 0400 /run/secrets/model_manager_username /run/model-manager/username
install -m 0400 /run/secrets/model_manager_password /run/model-manager/password
install -m 0400 /run/secrets/model_manager_ssh_key /run/model-manager/spark_key
install -m 0400 /run/secrets/model_manager_known_hosts /run/model-manager/known_hosts
chown -R 10001:10001 /run/model-manager
exec gosu 10001:10001 uvicorn app:app --host 0.0.0.0 --port 8080 --workers 1 --proxy-headers --forwarded-allow-ips "${MODEL_MANAGER_TRUSTED_PROXY_IP:?Set the exact Caddy edge IP}"
