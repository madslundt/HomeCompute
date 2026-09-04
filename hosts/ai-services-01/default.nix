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

  # SSH is the only remote path to this host: passwords and root login are
  # disabled, and `mads` holds passwordless sudo. Possession of the matching
  # private key is therefore an administrative credential, not a convenience.
  # Public keys are configuration; the private key never enters Git.
  # Fingerprint: SHA256:CSd6fvVEwnu25uHqRK4G1JSWi01nH7z2KpuoHXPQtOo
  users.users.mads.openssh.authorizedKeys.keys = [
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILT+ES2e5sbGFzBMLOWKZMawBm/kyadBthAldjAmK8Uc mads@ai-services-01-admin"
  ];

  # Enable after adding an encrypted host file and installing its age identity.
  homecompute.secrets.enable = false;

  # Enable after configuring a real off-host repository and password file.
  homecompute.backups.enable = false;
}
