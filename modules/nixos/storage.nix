{ ... }:
{
  users.groups.homecompute-state = { };
  users.groups.homecompute-secrets.gid = 989;

  systemd.tmpfiles.rules = [
    "d /srv/state 0750 root homecompute-state - -"
    "d /srv/state/control-plane 0750 root homecompute-state - -"
    "d /srv/state/control-plane/caddy-data 0750 root homecompute-state - -"
    "d /srv/state/control-plane/caddy-config 0750 root homecompute-state - -"
    "d /srv/state/control-plane/postgres-data 0750 root homecompute-state - -"
    "d /srv/state/control-plane/backups 0700 root root - -"
  ];
}
