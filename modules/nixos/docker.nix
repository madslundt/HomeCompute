{ ... }:
{
  imports = [ ./automation-network.nix ];

  virtualisation.docker = {
    enable = true;
    enableOnBoot = true;
    daemon.settings = {
      "default-address-pools" = [
        {
          # Keep automatic networks outside the 172.28.200.0/24 control-plane
          # subnet declared explicitly by Compose.
          base = "172.30.0.0/16";
          size = 24;
        }
      ];
      "live-restore" = true;
      "log-driver" = "local";
      "userland-proxy" = false;
    };
  };

  # Membership in the docker group is equivalent to root. Operators use sudo
  # for Compose rather than granting the interactive account daemon control.
}
