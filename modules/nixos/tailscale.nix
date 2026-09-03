{ config, ... }:
{
  services.tailscale = {
    enable = true;
    openFirewall = false;
    useRoutingFeatures = "client";
  };

  networking.firewall.allowedUDPPorts = [ config.services.tailscale.port ];
}
