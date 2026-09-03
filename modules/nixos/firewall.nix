{ ... }:
{
  networking.firewall = {
    enable = true;
    allowPing = false;
    checkReversePath = "loose";

    # Management and the HTTPS edge are reachable through Tailscale only by
    # default. Docker Compose must keep host publications on an explicit IP.
    interfaces."tailscale0".allowedTCPPorts = [
      22
      443
    ];
  };
}
