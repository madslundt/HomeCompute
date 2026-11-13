#!/bin/sh
set -eu

system_ca_bundle="${SSL_CERT_FILE:-/etc/ssl/certs/ca-certificates.crt}"
homecompute_root_ca="${HOMECOMPUTE_ROOT_CA:-/run/homecompute/home-core-root.crt}"
combined_ca_bundle="${PODWAVE_CA_BUNDLE:-/tmp/podwave-ca-bundle.crt}"

if [ ! -r "$system_ca_bundle" ]; then
    printf 'System CA bundle is unreadable: %s\n' "$system_ca_bundle" >&2
    exit 1
fi
if [ ! -r "$homecompute_root_ca" ]; then
    printf 'HomeCompute root CA is unreadable: %s\n' "$homecompute_root_ca" >&2
    exit 1
fi

cat "$system_ca_bundle" "$homecompute_root_ca" > "$combined_ca_bundle"
chmod 0600 "$combined_ca_bundle"
export SSL_CERT_FILE="$combined_ca_bundle"

exec "$@"
