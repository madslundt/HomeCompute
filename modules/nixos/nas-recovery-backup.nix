{ config, lib, pkgs, ... }:
let
  cfg = config.homecompute.nasRecoveryBackup;
  mount = "/mnt/homecompute-backups";
  state = "/var/lib/homecompute/nas-recovery-backup";
  runner = "${pkgs.python3}/bin/python3 ${../../scripts/nas-recovery-backup.py}";
  service = command: {
    requires = [ "network-online.target" ];
    after = [ "network-online.target" ];
    unitConfig.RequiresMountsFor = mount;
    path = [ pkgs.restic pkgs.util-linux ];
    serviceConfig = {
      Type = "oneshot";
      User = "root";
      UMask = "0077";
      ExecStart = "${runner} ${command}";
      TimeoutStartSec = "2h";
      MemoryMax = "1G";
      CPUQuota = "50%";
      IOSchedulingClass = "idle";
      Nice = 10;
      NoNewPrivileges = true;
      CapabilityBoundingSet = "";
      PrivateTmp = true;
      ProtectSystem = "strict";
      ProtectHome = true;
      ProtectKernelTunables = true;
      ProtectKernelModules = true;
      ProtectControlGroups = true;
      ReadWritePaths = [ mount state ];
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
  };
  config = lib.mkIf cfg.enable {
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
      timerConfig = { OnCalendar = "daily"; RandomizedDelaySec = "30m"; Persistent = true; };
    };
    systemd.timers.homecompute-nas-recovery-check = {
      wantedBy = [ "timers.target" ];
      timerConfig = { OnCalendar = "weekly"; RandomizedDelaySec = "2h"; Persistent = true; };
    };
  };
}
