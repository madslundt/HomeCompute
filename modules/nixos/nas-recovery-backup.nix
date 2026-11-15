{ config, lib, pkgs, ... }:
let
  cfg = config.homecompute.nasRecoveryBackup;
  mount = "/mnt/homecompute-backups";
  state = "/var/lib/homecompute/nas-recovery-backup";
  runner = "${pkgs.python3}/bin/python3 ${../../scripts/nas-recovery-backup.py}";
  backupCli = pkgs.writeShellScriptBin "homecompute-nas-recovery" ''
    exec ${pkgs.python3}/bin/python3 ${../../scripts/nas-recovery-backup.py} "$@"
  '';
  immichDatabaseBackup = builtins.toString ../../scripts/immich-db-backup.sh;
  immichCompose = builtins.toString ../../deploy/immich/compose.yaml;
  service = command: {
    requires = [ "network-online.target" ];
    after = [ "network-online.target" ];
    unitConfig = {
      RequiresMountsFor = mount;
      ConditionPathExists = "${state}/backup-authorized";
    };
    path = [ pkgs.restic pkgs.util-linux pkgs.docker pkgs.openssh pkgs.gzip pkgs.bash pkgs.python3 ];
    environment = {
      HOME = state;
      DOCKER_HOST = "unix:///var/run/docker.sock";
      DOCKER_CONFIG = "${state}/docker";
      PYTHONDONTWRITEBYTECODE = "1";
      IMMICH_COMPOSE_FILE = immichCompose;
    };
    serviceConfig = {
      Type = "oneshot";
      User = "root";
      UMask = "0077";
      UnsetEnvironment = [ "DOCKER_CONTEXT" "HTTP_PROXY" "HTTPS_PROXY" "ALL_PROXY" "http_proxy" "https_proxy" "all_proxy" "SSH_AUTH_SOCK" ];
      ExecStartPre = lib.optionals (command == "backup") [
        "${runner} validate"
      ];
      ExecStart = if command == "backup" then
        "${runner} scheduled"
        else "${runner} ${command}";
      Restart = "no";
      RestartForceExitStatus = lib.optional (command == "backup") 75;
      RestartSec = "30min";
      TimeoutStartSec = "2h";
      TimeoutStopSec = "10min";
      KillMode = "mixed";
      MemoryMax = "1G";
      CPUQuota = if command == "backup" then "100%" else "50%";
      IOSchedulingClass = "idle";
      Nice = 10;
      NoNewPrivileges = true;
      # Read private Immich and Hermes state without granting source writes.
      CapabilityBoundingSet = lib.optional (command == "backup") "CAP_DAC_READ_SEARCH";
      AmbientCapabilities = lib.optional (command == "backup") "CAP_DAC_READ_SEARCH";
      PrivateTmp = true;
      ProtectSystem = "strict";
      ProtectHome = "yes";
      BindReadOnlyPaths = lib.optionals (command == "backup") [
        "/run/secrets/hermes-backup/ssh-key"
        "/run/secrets/immich/database-password"
        "/etc/homecompute/immich.env"
        "/etc/homecompute/hermes-backup_known_hosts"
      ];
      ProtectKernelTunables = true;
      ProtectKernelModules = true;
      ProtectControlGroups = true;
      ReadOnlyPaths = lib.optionals (command == "backup") [ "/mnt/immich-nas" ];
      ReadWritePaths = [ mount state "/srv/backups/homecompute-selected-state"
        "/srv/state/immich/db-backups" ];
      RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" ];
    };
  };
in {
  options.homecompute.nasRecoveryBackup = {
    enable = lib.mkEnableOption "encrypted selected Immich and Hermes state backups on a separate NAS share";
    address = lib.mkOption { type = lib.types.str; default = "192.168.30.236"; };
    share = lib.mkOption { type = lib.types.nullOr lib.types.str; default = null; };
    credentialsFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Root-only mount.cifs credential file for the dedicated backup account.";
    };
  };
  config = lib.mkIf cfg.enable {
    environment.systemPackages = [ pkgs.cifs-utils backupCli ];
    assertions = [
      { assertion = cfg.share != null && cfg.credentialsFile != null;
        message = "NAS recovery backups need a separate known share and root-only account credential."; }
      { assertion = cfg.share == null || !(lib.any (word: lib.hasInfix word (lib.toLower cfg.share))
          [ "immich" "photo" "video" "library" "upload" ]);
        message = "Recovery backups must not use an Immich/media share."; }
    ];
    environment.etc."homecompute/nas-recovery-backup.json".text = builtins.toJSON {
      inherit (cfg) address share credentialsFile;
      hermesSshKeyFile = "/run/secrets/hermes-backup/ssh-key";
      hermesKnownHostsFile = "/etc/homecompute/hermes-backup_known_hosts";
      immichDbBackupScript = immichDatabaseBackup;
      immichComposeFile = immichCompose;
    };
    environment.etc."homecompute/hermes-backup_known_hosts".source = ../../config/hermes-backup-known-hosts;
    systemd.tmpfiles.rules = [
      "d ${state} 0700 root root - -"
      "d /srv/backups/homecompute-selected-state 0700 root root - -"
      "d /srv/restore-tests 0700 root root - -"
      "d /var/lib/homecompute 0750 root root - -"
      "f /var/lib/homecompute/deploy.lock 0600 root root - -"
      "d /run/homecompute 0755 root root - -"
    ];
    fileSystems.${mount} = {
      device = "//${cfg.address}/${if cfg.share == null then "UNCONFIGURED" else cfg.share}";
      fsType = "cifs";
      options = [ "noauto" "x-systemd.automount" "_netdev" "nofail" "vers=3.1.1"
        "credentials=${if cfg.credentialsFile == null then "/nonexistent" else cfg.credentialsFile}"
        "uid=0" "gid=0" "file_mode=0600" "dir_mode=0700" "nosuid" "nodev" "noexec"
        "x-systemd.mount-timeout=15s" ];
    };
    systemd.services.homecompute-nas-recovery-backup = service "backup";
    systemd.services.homecompute-nas-recovery-initialize = service "init";
    systemd.services.homecompute-nas-recovery-proof = service "proof";
    systemd.services.homecompute-nas-recovery-check = service "check";
    systemd.timers.homecompute-nas-recovery-backup = {
      wantedBy = [ "timers.target" ];
      # Separate from the local VM bootstrap snapshot's midnight window.
      timerConfig = { OnCalendar = "*-*-* 03:15:00"; RandomizedDelaySec = "30m"; Persistent = true; };
    };
    systemd.timers.homecompute-nas-recovery-check = {
      wantedBy = [ "timers.target" ];
      timerConfig = { OnCalendar = "weekly"; RandomizedDelaySec = "2h"; Persistent = true; };
    };
  };
}
