{ lib, ... }:
{
  imports = [
    ./hardware-configuration.nix
    ../../modules/nixos/application-config.nix
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

  networking.hostName = "home-core";

  # Preserve the installed DHCP connection while bootstrapping over LAN SSH.
  # enp44s0 (84:47:09:79:58:b1) is cabled; enp45s0 is reserved for GB10.
  networking.networkmanager.enable = lib.mkForce true;
  networking.useDHCP = lib.mkForce false;
  services.resolved.enable = lib.mkForce false;
  networking.firewall.interfaces.enp44s0.allowedTCPPorts = [ 22 ];

  # This installed host has a 1 GiB ESP. Keep room for future kernels.
  boot.loader.systemd-boot.configurationLimit = lib.mkForce 5;

  # SSH is the only remote path to this host: passwords and root login are
  # disabled, and `mads` holds passwordless sudo. Possession of the matching
  # private key is therefore an administrative credential, not a convenience.
  # Public keys are configuration; the private key never enters Git.
  # Fingerprint: SHA256:CSd6fvVEwnu25uHqRK4G1JSWi01nH7z2KpuoHXPQtOo
  users.users.mads.openssh.authorizedKeys.keys = [
    "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILT+ES2e5sbGFzBMLOWKZMawBm/kyadBthAldjAmK8Uc mads@home-core-admin"
  ];

  homecompute.secrets = {
    enable = true;
    defaultSopsFile = ../../secrets/home-core.sops.yaml;
  };

  # Enable after configuring a real off-host repository and password file.
  homecompute.backups.enable = false;
}
