{ ... }:
{
  imports = [
    ./hardware-configuration.nix
    ../../modules/nixos/system.nix
    ../../modules/nixos/networking.nix
    ../../modules/nixos/firewall.nix
    ../../modules/nixos/docker.nix
    ../../modules/nixos/ssh.nix
    ../../modules/nixos/tailscale.nix
    ../../modules/nixos/storage.nix
    ../../modules/nixos/backups.nix
    ../../modules/nixos/secrets.nix
  ];

  networking.hostName = "ai-services-01";

  # Enable after adding an encrypted host file and installing its age identity.
  homecompute.secrets.enable = false;

  # Enable after configuring a real off-host repository and password file.
  homecompute.backups.enable = false;
}
