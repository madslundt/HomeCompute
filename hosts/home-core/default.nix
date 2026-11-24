{
  config,
  lib,
  pkgs,
  ...
}:
let
  openbotNetwork = builtins.fromJSON (builtins.readFile ../../config/openbot-network.json);
in
{
  imports = [
    ./hardware-configuration.nix
    ../../modules/nixos/application-config.nix
    ../../modules/nixos/agent-office.nix
    ../../modules/nixos/assistant-context.nix
    ../../modules/nixos/android-gateway.nix
    ../../modules/nixos/hermes-dashboard.nix
    ../../modules/nixos/system.nix
    ../../modules/nixos/networking.nix
    ../../modules/nixos/firewall.nix
    ../../modules/nixos/compute-link.nix
    ../../modules/nixos/compute-ssh-tunnel.nix
    ../../modules/nixos/wyoming-stt-network.nix
    ../../modules/nixos/platform-monitoring.nix
    ../../modules/nixos/service-recovery.nix
    ../../modules/nixos/model-update-monitor.nix
    ../../modules/nixos/agent-harness.nix
    ../../modules/nixos/mattermost.nix
    ../../modules/nixos/opencode.nix
    ../../modules/nixos/agents-vm.nix
    ../../modules/nixos/agents-vm-backup.nix
    ../../modules/nixos/toolbox-vm.nix
    ../../modules/nixos/nod-vm.nix
    ../../modules/nixos/docker.nix
    ../../modules/nixos/ssh.nix
    ../../modules/nixos/tailscale.nix
    ../../modules/nixos/gateway-certificate.nix
    ../../modules/nixos/ttlock-webhook.nix
    ../../modules/nixos/storage.nix
    ../../modules/nixos/backups.nix
    ../../modules/nixos/nas-recovery-backup.nix
    ../../modules/nixos/orca-backup.nix
    ../../modules/nixos/immich.nix
    ../../modules/nixos/secrets.nix
  ];

  networking.hostName = "home-core";
  homecompute.hermesDashboard.enable = true;
  homecompute.androidGateway = {
    enable = true;
    portalHost = "192.168.30.34";
  };
  homecompute.mattermost.enable = true;
  homecompute.opencode.enable = true;
  homecompute.serviceRecovery.enable = true;
  homecompute.nodVm.enable = true;

  homecompute.toolboxVm = {
    enable = true;
    sshKeys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILT+ES2e5sbGFzBMLOWKZMawBm/kyadBthAldjAmK8Uc mads@home-core-admin"
    ];
  };
  # Household admission follows the isolated encrypted-NAS restore and current
  # deletion replay proof. Delivery and external publication stay separately gated.
  homecompute.assistantContext = {
    enable = true;
    mode = lib.mkDefault "shadow";
    dataClassification = "household";
    restoreVerified = true;
    sync = {
      enable = true;
      caFile = "/etc/homecompute/daythread-assistant-context-ca.crt";
    };
    inference = {
      embedding = true;
      briefings = true;
    };
  };
  homecompute.nasRecoveryBackup = {
    enable = true;
    share = "homecompute-backups";
    credentialsFile = "/etc/homecompute/nas-recovery-backup.credentials";
    includeApplicationState = false;
    selectedState = true;
  };

  # Preserve the installed DHCP connection while bootstrapping over LAN SSH.
  # enp44s0 (84:47:09:79:58:b1) is cabled; enp45s0 is reserved for GB10.
  networking.networkmanager.enable = lib.mkForce true;
  networking.useDHCP = lib.mkForce false;
  services.resolved.enable = lib.mkForce false;
  networking.firewall.interfaces.enp44s0.allowedTCPPorts = [
    22
  ];

  # Preserve the supervised notification relay for the existing n8n client.
  networking.firewall.extraCommands = lib.mkAfter ''
    iptables -w -C INPUT -i br-hc-n8n -s 172.28.201.2/32 -d 172.28.201.1/32 \
      -p tcp --dport 19443 -m conntrack --ctstate NEW,ESTABLISHED \
      -m comment --comment hc-openclaw-communication-supervised -j ACCEPT 2>/dev/null || \
      iptables -w -I INPUT 1 -i br-hc-n8n -s 172.28.201.2/32 -d 172.28.201.1/32 \
        -p tcp --dport 19443 -m conntrack --ctstate NEW,ESTABLISHED \
        -m comment --comment hc-openclaw-communication-supervised -j ACCEPT
  '';
  networking.firewall.extraStopCommands = lib.mkAfter ''
    while iptables -w -C INPUT -i br-hc-n8n -s 172.28.201.2/32 -d 172.28.201.1/32 \
      -p tcp --dport 19443 -m conntrack --ctstate NEW,ESTABLISHED \
      -m comment --comment hc-openclaw-communication-supervised -j ACCEPT 2>/dev/null; do
      iptables -w -D INPUT -i br-hc-n8n -s 172.28.201.2/32 -d 172.28.201.1/32 \
        -p tcp --dport 19443 -m conntrack --ctstate NEW,ESTABLISHED \
        -m comment --comment hc-openclaw-communication-supervised -j ACCEPT
    done
  '';

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
    libraryPath = "/mnt/immich-nas";
    lanAddress = "192.168.30.122";
    tailscaleAddress = "100.110.248.102";
    port = 2283;
    # Immich originals live on the Synology SMB share. PostgreSQL and database
    # dumps stay on local NVMe.
    synologyMount.enable = true;
    synologyMount.mountPath = "/mnt/immich-nas";
    synologyMount.address = "192.168.30.236";
    synologyMount.share = "immich-library";
  };

  homecompute.agentsVm = {
    enable = true;
    dataClassification = "household";
    restoreVerified = true;
    # Budget 8 GiB for Hermes, 4 GiB for Chromium, and 4 GiB for
    # guest services and browser proxies.
    resources.memoryMiB = 16384;
    resources.diskGiB = 200;
    sshAuthorizedKeys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILT+ES2e5sbGFzBMLOWKZMawBm/kyadBthAldjAmK8Uc mads@home-core-admin"
    ];

    # Public browsing and versioned image pulls are explicitly allowed; private
    # LAN, compute, link-local, and tailnet destinations remain rejected.
    # OpenBot TURN is restricted to the current public relay IPs in its
    # canonical network declaration; container egress is filtered separately.
    network.allowedInternetTcpPorts = [ 80 443 ];
    network.openbotTurnAddresses = map (server: server.address) openbotNetwork.turn_servers;
    network.maintenanceEgress = false;
  };

  # Enable only after adding ttlock-webhook/environment to the encrypted SOPS
  # document and confirming the account-specific TTLock callback contract.
  homecompute.ttlockWebhook.enable = true;
}
