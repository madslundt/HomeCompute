{ pkgs, ... }:
let
  directory = "/srv/state/control-plane/caddy-data/token-usage";
in
{
  systemd.tmpfiles.rules = [ "d ${directory} 0755 root root - -" ];
  systemd.services.homecompute-token-usage = {
    description = "Publish aggregate-only local model token usage";
    after = [ "docker.service" ];
    requires = [ "docker.service" ];
    path = [ pkgs.docker ];
    environment.TZDIR = "${pkgs.tzdata}/share/zoneinfo";
    serviceConfig = {
      Type = "oneshot";
      User = "root";
      ExecStart = "${pkgs.python3}/bin/python3 ${../../scripts/token-usage.py} --publish ${directory}/usage.json";
      TimeoutStartSec = "50s";
      UMask = "0022";
      NoNewPrivileges = true;
      PrivateTmp = true;
      ProtectHome = true;
      ProtectSystem = "strict";
      ReadWritePaths = [ directory ];
      RestrictAddressFamilies = [ "AF_UNIX" ];
    };
  };
  systemd.timers.homecompute-token-usage = {
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "2min";
      OnUnitActiveSec = "60s";
      AccuracySec = "5s";
    };
  };
}
