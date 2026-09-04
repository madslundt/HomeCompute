{ config, ... }:
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
}
