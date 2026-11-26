# Retire the Orca pilot

Deploy the reviewed retirement release to Home Core. The NixOS generation
removes the backup timer and the Compose release removes the Caddy routes and
Homepage card. The release then takes one final cold backup from the last
running deployment, removes the Orca project, its networks and locally tagged
images, and revokes the dedicated LiteLLM keys. The runtime profile and
non-inference configuration move to root-only recovery directories
`/srv/state/.retired/orca` and `/var/lib/homecompute/.retired/orca`. Existing
archives remain under `/srv/backups/agent-recovery/orca`.

Use the [Paseo lifecycle](../../deploy/paseo/README.md#deploy-or-upgrade)
to bring its GitHub Projects reader to this release if Paseo is enrolled.

UniFi owns the retired `orca.home.arpa` A record (formerly `192.168.30.122`,
TTL 300). Remove that exact record from UniFi's Network DNS policy. The
shared `home.arpa` split-DNS route in Tailscale continues to serve other
HomeCompute names. Remove any independently configured Orca-only URL or
Serve route if present. If the pilot had separately issued a GitHub Projects
token or enrolled a cloud provider, revoke those grants in their respective
owner accounts.

Verify the deployed revision with `./scripts/homecompute status`,
`services`, `doctor`, and `drift`. Confirm the old containers, Docker networks
and systemd units are absent, the Homepage card is gone, and the hostname
has no A record from both the LAN resolver and Tailscale DNS. Check a
neighboring service such as LiteLLM to verify the shared DNS and gateway.

To recover the pilot after an intentional rollback, restore the retired
profile to `/srv/state/orca` with owner and group 12074 and mode 0700, or
extract a checksum-verified cold archive there. Deploy the previous reviewed
release to reprovision the dedicated inference key. Restore DNS and
separately provision or reauthorize any external credentials still needed.
