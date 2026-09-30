{
  config,
  lib,
  ...
}:
{
  imports = [
    ./hardware-configuration.nix
    ../../modules/nixos/application-config.nix
    ../../modules/nixos/system.nix
    ../../modules/nixos/networking.nix
    ../../modules/nixos/firewall.nix
    ../../modules/nixos/compute-link.nix
    ../../modules/nixos/compute-ssh-tunnel.nix
    ../../modules/nixos/wyoming-stt-network.nix
    ../../modules/nixos/platform-monitoring.nix
    ../../modules/nixos/model-update-monitor.nix
    ../../modules/nixos/agent-harness.nix
    ../../modules/nixos/agents-vm.nix
    ../../modules/nixos/agents-vm-backup.nix
    ../../modules/nixos/docker.nix
    ../../modules/nixos/ssh.nix
    ../../modules/nixos/tailscale.nix
    ../../modules/nixos/ttlock-webhook.nix
    ../../modules/nixos/storage.nix
    ../../modules/nixos/backups.nix
    ../../modules/nixos/immich.nix
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

  # Emergency inference transport only. The dedicated enp45s0 link remains
  # preferred. Root may set this true after provisioning the restricted key,
  # pinned known_hosts file, and forwarding-only home-spark account.
  # The dedicated compute link is physically down. Keep the narrowly scoped
  # SSH fallback active until the link is restored and requalified.
  homecompute.computeSshTunnel.enable = lib.mkDefault true;

  # The production backup contract remains explicitly off-host and is not
  # satisfied by the temporary same-host agents bootstrap repository below.
  homecompute.backups.enable = false;
  homecompute.backups.destinations.hetzner = {
    # Replace the account and hostname after purchasing the dedicated BX11.
    repositoryBase = "sftp:uXXXXX@uXXXXX.your-storagebox.de:/backups/homecompute";
    passwordFile = "/run/secrets/restic/hetzner/password";
    sshPrivateKeyFile = "/run/secrets/restic/hetzner/ssh-key";
    knownHostsFile = "/etc/homecompute/restic/hetzner_known_hosts";
  };
  homecompute.backups.sources.immich = {
    destinations = [ "hetzner" ];
    paths = [ config.homecompute.immich.libraryPath "${config.homecompute.immich.stateRoot}/db-backups" ];
    prepareCommand = ../../scripts/immich-db-backup.sh;
    retention = {
      daily = 30;
      weekly = 12;
      monthly = 24;
      yearly = 10;
    };
    schedule = "daily";
    randomizedDelay = "30m";
  };

  # The database credential is provisioned in SOPS. Enable Takeout import
  # separately after creating an import-scoped API key in Immich.
  homecompute.immich = {
    enable = true;
    importEnabled = false;
    stateRoot = "/srv/state/immich";
    libraryPath = "/srv/state/immich/library";
    lanAddress = "192.168.30.122";
    tailscaleAddress = "100.110.248.102";
    port = 2283;
  };

  homecompute.agentsVm = {
    enable = true;
    dataClassification = "synthetic-only";
    sshAuthorizedKeys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILT+ES2e5sbGFzBMLOWKZMawBm/kyadBthAldjAmK8Uc mads@home-core-admin"
    ];

    # Package/bootstrap access only. Return this to false once the pinned
    # NemoClaw tuple has been installed and the canary is healthy.
    # Temporary for the synthetic canary: NemoClaw 0.0.129 needs GHCR access
    # to recreate its managed sandbox after a VM lifecycle event.
    network.maintenanceEgress = true;

    localBootstrapBackup = {
      enable = true;
      riskAccepted = true;
      passwordFile = config.sops.secrets."restic/password".path;
    };
  };

  # Enable only after adding ttlock-webhook/environment to the encrypted SOPS
  # document and confirming the account-specific TTLock callback contract.
  homecompute.ttlockWebhook.enable = true;
}
