{ config, lib, pkgs, ... }:
let
  cfg = config.homecompute.nasRecoveryBackup;
  mount = "/mnt/homecompute-backups";
  state = "/var/lib/homecompute/nas-recovery-backup";
  runner = "${pkgs.python3}/bin/python3 ${../../scripts/nas-recovery-backup.py}";
  service = command: {
    requires = [ "network-online.target" ];
    after = [ "network-online.target" ];
    unitConfig = {
      RequiresMountsFor = mount;
      ConditionPathExists = "${state}/backup-authorized";
    };
    path = [ pkgs.restic pkgs.util-linux pkgs.docker pkgs.systemd ];
    environment = {
      HOME = state;
      DOCKER_HOST = "unix:///var/run/docker.sock";
      DOCKER_CONFIG = "${state}/docker";
      PYTHONDONTWRITEBYTECODE = "1";
    };
    serviceConfig = {
      Type = "oneshot";
      User = "root";
      UMask = "0077";
      UnsetEnvironment = [ "DOCKER_CONTEXT" "HTTP_PROXY" "HTTPS_PROXY" "ALL_PROXY" "http_proxy" "https_proxy" "all_proxy" "SSH_AUTH_SOCK" ];
      ExecStartPre = lib.optionals (command == "backup" && cfg.includeApplicationState) [
        "${runner} validate"
        "${pkgs.python3}/bin/python3 ${../../scripts/application-recovery.py} prepare"
      ];
      ExecStart = "${runner} ${command}";
      ExecStopPost = lib.optionals (command == "backup" && cfg.includeApplicationState) [
        "${pkgs.python3}/bin/python3 ${../../scripts/application-recovery.py} resume"
      ];
      TimeoutStartSec = "2h";
      TimeoutStopSec = "10min";
      KillMode = "mixed";
      MemoryMax = "1G";
      CPUQuota = if command == "backup" then "100%" else "50%";
      IOSchedulingClass = "idle";
      Nice = 10;
      NoNewPrivileges = true;
      # Root must read private application/VM directories owned by their
      # service UIDs; grant read/search, never write/ownership overrides.
      CapabilityBoundingSet = lib.optional (command == "backup" && cfg.includeApplicationState) "CAP_DAC_READ_SEARCH";
      AmbientCapabilities = lib.optional (command == "backup" && cfg.includeApplicationState) "CAP_DAC_READ_SEARCH";
      PrivateTmp = true;
      ProtectSystem = "strict";
      ProtectHome = if command == "backup" && cfg.includeApplicationState then "tmpfs" else "yes";
      BindReadOnlyPaths = lib.optionals (command == "backup" && cfg.includeApplicationState) [
        "/home/mads/projects/chessmate/config/certificates"
        "/home/mads/projects/chessmate/config/.env"
      ];
      ProtectKernelTunables = true;
      ProtectKernelModules = true;
      ProtectControlGroups = true;
      ReadWritePaths = [ mount state "/srv/backups/agent-recovery" "/var/lib/homecompute/deploy.lock" "/run/homecompute" ];
      RestrictAddressFamilies = [ "AF_UNIX" ];
    };
  };
in {
  options.homecompute.nasRecoveryBackup = {
    enable = lib.mkEnableOption "encrypted completed agent recovery archives on a separate NAS share";
    address = lib.mkOption { type = lib.types.str; default = "192.168.30.236"; };
    share = lib.mkOption { type = lib.types.nullOr lib.types.str; default = null; };
    credentialsFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Root-only mount.cifs credential file for the dedicated backup account.";
    };
    includeApplicationState = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Prepare a consistent recovery archive of gateway, n8n, Mattermost,
        Immich and Chessmate databases, local OpenWebUI/Omi/book state,
        trusted agent state and both VMs before transfer.
        This briefly stops applications and VMs; active private browsers defer
        the job. Original NAS media and the host age identity remain separate.
      '';
    };
  };
  config = lib.mkIf cfg.enable {
    environment.systemPackages = [ pkgs.qemu_kvm pkgs.cifs-utils ];
    assertions = [
      { assertion = cfg.share != null && cfg.credentialsFile != null;
        message = "NAS recovery backups need a separate known share and root-only account credential."; }
      { assertion = cfg.share == null || !(lib.any (word: lib.hasInfix word (lib.toLower cfg.share))
          [ "immich" "photo" "video" "library" "upload" ]);
        message = "Recovery backups must not use an Immich/media share."; }
    ];
    environment.etc."homecompute/nas-recovery-backup.json".text = builtins.toJSON {
      inherit (cfg) address share credentialsFile;
    };
    systemd.tmpfiles.rules = [
      "d ${state} 0700 root root - -"
      "d /srv/backups/agent-recovery 0700 root root - -"
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
