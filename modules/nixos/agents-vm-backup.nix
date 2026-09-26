{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homecompute.agentsVm.localBootstrapBackup;
  repository = "/srv/backup/restic-homecompute";
  source = "/srv/state/agents-vm";
  marker = "/run/homecompute/agents-vm-backup-was-active";
  lock = "/run/homecompute/agents-vm-backup-in-progress";
  minimumFreeBytes = cfg.minimumFreeGiB * 1024 * 1024 * 1024;
in
{
  options.homecompute.agentsVm.localBootstrapBackup = {
    enable = lib.mkEnableOption "temporary same-host Restic backup for the agents VM";

    riskAccepted = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Explicit acknowledgement that this repository does not survive loss of
        home-core or its storage and is suitable only for synthetic pilot data.
      '';
    };

    passwordFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      description = "Runtime path to the sops-managed Restic password.";
    };

    minimumFreeGiB = lib.mkOption {
      type = lib.types.ints.positive;
      default = 100;
      description = "Minimum free space required on the local repository filesystem.";
    };
  };

  config = lib.mkIf cfg.enable (
    lib.mkMerge [
      {
        assertions = [
          {
            assertion = cfg.riskAccepted;
            message = "agentsVm.localBootstrapBackup requires riskAccepted = true";
          }
          {
            assertion = cfg.passwordFile != null;
            message = "agentsVm.localBootstrapBackup.passwordFile must reference a runtime secret";
          }
        ];

        systemd.tmpfiles.rules = [
          "d /srv/backup 0700 root root - -"
          "d ${repository} 0700 root root - -"
          "d /run/homecompute 0755 root root - -"
        ];
      }

      (lib.mkIf (cfg.passwordFile != null) {
        services.restic.backups.agents-vm-bootstrap = {
          initialize = true;
          inherit repository;
          passwordFile = cfg.passwordFile;
          paths = [ source ];
          backupPrepareCommand = ''
            if [ ! -d ${repository} ] || [ -L ${repository} ]; then
              echo "local bootstrap repository must be a real directory" >&2
              exit 1
            fi
            if [ "$(${pkgs.coreutils}/bin/stat -c '%u:%g:%a' ${repository})" != "0:0:700" ]; then
              echo "local bootstrap repository must be root-owned mode 0700" >&2
              exit 1
            fi
            if [ ! -s ${cfg.passwordFile} ]; then
              echo "Restic password is absent or empty" >&2
              exit 1
            fi
            password_metadata="$(${pkgs.coreutils}/bin/stat -Lc '%u:%g:%a' ${cfg.passwordFile})"
            case "$password_metadata" in
              0:0:400|0:0:600) ;;
              *) echo "Restic password must be root-owned and root-only" >&2; exit 1 ;;
            esac
            available_bytes="$(${pkgs.coreutils}/bin/df --output=avail -B1 ${repository} | ${pkgs.coreutils}/bin/tail -n 1)"
            if [ "$available_bytes" -lt ${toString minimumFreeBytes} ]; then
              echo "local bootstrap repository has less than ${toString cfg.minimumFreeGiB} GiB free" >&2
              exit 1
            fi

            ${pkgs.coreutils}/bin/touch ${lock}
            ${pkgs.coreutils}/bin/rm -f ${marker}
            if ${pkgs.systemd}/bin/systemctl is-active --quiet homecompute-agents-vm.service; then
              ${pkgs.coreutils}/bin/touch ${marker}
              if ! ${pkgs.systemd}/bin/systemctl stop homecompute-agents-vm.service; then
                ${pkgs.coreutils}/bin/rm -f ${lock}
                ${pkgs.coreutils}/bin/rm -f ${marker}
                exit 1
              fi
            fi
            load_state="$(${pkgs.systemd}/bin/systemctl show --property=LoadState --value homecompute-agents-vm.service 2>/dev/null || echo not-found)"
            if [ "$load_state" = not-found ]; then
              active_state=inactive
              main_pid=0
            else
              active_state="$(${pkgs.systemd}/bin/systemctl show --property=ActiveState --value homecompute-agents-vm.service)"
              main_pid="$(${pkgs.systemd}/bin/systemctl show --property=MainPID --value homecompute-agents-vm.service)"
            fi
            if [ "$active_state" != inactive ] || [ "$main_pid" != 0 ]; then
              ${pkgs.coreutils}/bin/rm -f ${lock} ${marker}
              echo "agents VM or its QEMU process remained active; refusing an inconsistent backup" >&2
              exit 1
            fi
            if [ -e ${source}/agents.qcow2 ]; then
              ${pkgs.qemu_kvm}/bin/qemu-img check -q ${source}/agents.qcow2
            fi
          '';
          backupCleanupCommand = ''
            ${pkgs.coreutils}/bin/rm -f ${lock}
            if [ -e ${marker} ]; then
              ${pkgs.systemd}/bin/systemctl start homecompute-agents-vm.service
              ${pkgs.coreutils}/bin/rm -f ${marker}
            fi
          '';
          pruneOpts = [
            "--keep-daily 7"
            "--keep-weekly 4"
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
