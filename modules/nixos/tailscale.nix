{ config, pkgs, ... }:
{
  services.tailscale = {
    enable = true;
    openFirewall = false;
    useRoutingFeatures = "both";
    extraSetFlags = [
      "--advertise-exit-node"
      "--advertise-routes=192.168.30.0/24"
    ];
  };

  networking.firewall.allowedUDPPorts = [ config.services.tailscale.port ];

  systemd.services.homecompute-chessmate-tailscale = {
    description = "Private Tailscale HTTPS access to Chessmate";
    after = [ "tailscaled.service" "docker.service" ];
    wants = [ "tailscaled.service" ];
    wantedBy = [ "multi-user.target" ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
      ExecStart = "${pkgs.tailscale}/bin/tailscale serve --bg --yes --https=18080 http://192.168.30.122:8080";
      ExecStop = "${pkgs.tailscale}/bin/tailscale serve --yes --https=18080 off";
    };
  };

}
