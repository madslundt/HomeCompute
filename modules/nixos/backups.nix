{
  config,
  lib,
  ...
}:
let
  cfg = config.homecompute.backups;
in
{
  options.homecompute.backups = {
    enable = lib.mkEnableOption "encrypted off-host HomeCompute backups";

    repository = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Restic repository URL or absolute path.";
    };

    passwordFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Runtime path to the Restic repository password.";
    };

    prepareCommand = lib.mkOption {
      type = lib.types.nullOr lib.types.lines;
      default = null;
      description = "Command that creates application-consistent backup artifacts before Restic runs.";
    };

    paths = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "/srv/state" ];
      description = "Persistent paths included in the off-host backup.";
    };
  };

  config = lib.mkIf cfg.enable (
    lib.mkMerge [
      {
        assertions = [
          {
            assertion = cfg.repository != null;
            message = "homecompute.backups.repository must name an off-host repository";
          }
          {
            assertion = cfg.passwordFile != null;
            message = "homecompute.backups.passwordFile must reference a runtime secret";
          }
          {
            assertion = cfg.prepareCommand != null;
            message = "homecompute.backups.prepareCommand must create an application-consistent snapshot or database dump";
          }
        ];
      }

      (lib.mkIf (cfg.repository != null && cfg.passwordFile != null && cfg.prepareCommand != null) {
        services.restic.backups.homecompute = {
          initialize = false;
          repository = cfg.repository;
          passwordFile = cfg.passwordFile;
          backupPrepareCommand = cfg.prepareCommand;
          paths = cfg.paths;
          pruneOpts = [
            "--keep-daily 7"
            "--keep-weekly 5"
            "--keep-monthly 12"
          ];
          timerConfig = {
            OnCalendar = "daily";
            Persistent = true;
            RandomizedDelaySec = "30m";
          };
        };
      })
    ]
  );
}
