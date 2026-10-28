{ pkgs, ... }:
{
  systemd.services.homecompute-gateway-certificate = {
    description = "Renew the public Tailscale certificate for the private LLM gateway";
    wants = [ "network-online.target" "tailscaled.service" ];
    after = [ "network-online.target" "tailscaled.service" "docker.service" ];
    path = [ pkgs.tailscale pkgs.docker ];
    serviceConfig = {
      Type = "oneshot";
      User = "root";
      UMask = "0077";
      TimeoutStartSec = "5min";
      ExecStart = "${pkgs.python3}/bin/python3 ${../../scripts/renew-gateway-certificate.py}";
    };
  };
  systemd.timers.homecompute-gateway-certificate = {
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "daily";
      RandomizedDelaySec = "1h";
      Persistent = true;
    };
  };
}
