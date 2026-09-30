{ config, lib, pkgs, ... }:
let
  cfg = config.homecompute.backups;

  destinationType = lib.types.submodule ({ ... }: {
    options = {
      repositoryBase = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "Repository base; each source receives a separate repository below it.";
      };
      passwordFile = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "Runtime Restic password file.";
      };
      sshPrivateKeyFile = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "Optional dedicated SSH identity for SFTP destinations.";
      };
      knownHostsFile = lib.mkOption {
        type = lib.types.nullOr lib.types.str;
        default = null;
        description = "Pinned SSH host keys used with strict host checking.";
      };
    };
  });

  sourceType = lib.types.submodule ({ ... }: {
    options = {
      destinations = lib.mkOption {
        type = lib.types.listOf lib.types.str;
        default = [ ];
        description = "Named backup destinations receiving this source.";
      };
      paths = lib.mkOption {
        type = lib.types.listOf lib.types.str;
        default = [ ];
        description = "Durable application paths included in this source.";
      };
      prepareCommand = lib.mkOption {
        type = lib.types.nullOr lib.types.lines;
        default = null;
        description = "Application-consistent preparation command, run before each backup.";
      };
      retention = {
        daily = lib.mkOption { type = lib.types.ints.unsigned; default = 7; };
        weekly = lib.mkOption { type = lib.types.ints.unsigned; default = 5; };
        monthly = lib.mkOption { type = lib.types.ints.unsigned; default = 12; };
        yearly = lib.mkOption { type = lib.types.ints.unsigned; default = 0; };
      };
      schedule = lib.mkOption {
        type = lib.types.str;
        default = "daily";
        description = "systemd OnCalendar schedule for this source.";
      };
      randomizedDelay = lib.mkOption {
        type = lib.types.str;
        default = "30m";
        description = "systemd randomized delay for this source.";
      };
    };
  });

  resticJob = sourceName: destinationName: source: destination:
    let
      jobName = "homecompute-${sourceName}-${destinationName}";
      repository = "${destination.repositoryBase}/${sourceName}";
      retention = lib.optional (source.retention.daily > 0) "--keep-daily ${toString source.retention.daily}"
        ++ lib.optional (source.retention.weekly > 0) "--keep-weekly ${toString source.retention.weekly}"
        ++ lib.optional (source.retention.monthly > 0) "--keep-monthly ${toString source.retention.monthly}"
        ++ lib.optional (source.retention.yearly > 0) "--keep-yearly ${toString source.retention.yearly}";
      sshOptions = lib.optional (destination.sshPrivateKeyFile != null) (
        "sftp.command='ssh -i ${destination.sshPrivateKeyFile} "
        + "-o IdentitiesOnly=yes -o BatchMode=yes -o StrictHostKeyChecking=yes "
        + "-o UserKnownHostsFile=${destination.knownHostsFile} -s sftp'"
      );
      common = {
        inherit repository;
        passwordFile = destination.passwordFile;
        initialize = false;
        extraOptions = sshOptions;
      };
    in
    {
      "${jobName}" = common // {
        paths = source.paths;
        backupPrepareCommand = source.prepareCommand;
        pruneOpts = retention;
        timerConfig = {
          OnCalendar = source.schedule;
          Persistent = true;
          RandomizedDelaySec = source.randomizedDelay;
        };
      };
      "${jobName}-check" = common // {
        paths = [ ];
        pruneOpts = [ ];
        checkOpts = [ ];
        runCheck = true;
        timerConfig = {
          OnCalendar = "weekly";
          Persistent = true;
          RandomizedDelaySec = "2h";
        };
      };
    };

  jobs = lib.foldl' lib.recursiveUpdate { }
    (lib.concatMap (sourceName:
      let source = cfg.sources.${sourceName};
      in lib.concatMap (destinationName:
        resticJob sourceName destinationName source cfg.destinations.${destinationName}
      ) source.destinations
    ) (builtins.attrNames cfg.sources));

  statusHook = sourceName: kind: resticWrapper: pkgs.writeShellScript "homecompute-${sourceName}-${kind}-status" ''
    set -eu
    directory=/var/lib/homecompute/backup-status
    install -d -m 0700 "$directory"
    result="''${SERVICE_RESULT:-unknown}"
    timestamp="$(${pkgs.coreutils}/bin/date -u +%Y-%m-%dT%H:%M:%SZ)"
    repository_size=null
    if [ "$result" = success ] && [ "${kind}" = backup ]; then
      repository_size="$(/run/current-system/sw/bin/${resticWrapper} stats --mode raw-data --json latest 2>/dev/null \
        | ${pkgs.jq}/bin/jq -er .total_size 2>/dev/null)" || repository_size=null
    fi
    if [ "$result" = success ]; then
      target="$directory/${sourceName}-last-${kind}"
      ${pkgs.coreutils}/bin/rm -f "$directory/${sourceName}-last-${kind}-failure"
    else
      target="$directory/${sourceName}-last-${kind}-failure"
    fi
    temporary="$(${pkgs.coreutils}/bin/mktemp "$directory/.${sourceName}-${kind}.XXXXXX")"
    printf '{"time":"%s","result":"%s","repository_size_bytes":%s}\n' \
      "$timestamp" "$result" "$repository_size" >"$temporary"
    chmod 0600 "$temporary"
    ${pkgs.coreutils}/bin/mv -f "$temporary" "$target"
  '';

  statusUnitConfig = lib.foldl' lib.recursiveUpdate { }
    (lib.concatMap (sourceName:
      let
        source = cfg.sources.${sourceName};
        backupUnits = map (destinationName:
          let resticWrapper = "restic-homecompute-${sourceName}-${destinationName}";
          in {
          "restic-backups-homecompute-${sourceName}-${destinationName}" = {
            serviceConfig.ExecStopPost = "${statusHook sourceName "backup" resticWrapper}";
          };
          "restic-backups-homecompute-${sourceName}-${destinationName}-check" = {
            serviceConfig.ExecStopPost = "${statusHook sourceName "check" resticWrapper}";
          };
        }) source.destinations;
      in backupUnits
    ) (builtins.attrNames cfg.sources));
in
{
  options.homecompute.backups = {
    enable = lib.mkEnableOption "encrypted off-host HomeCompute backups";
    destinations = lib.mkOption {
      type = lib.types.attrsOf destinationType;
      default = { };
      description = "Independent Restic destinations, such as Hetzner Storage Box.";
    };
    sources = lib.mkOption {
      type = lib.types.attrsOf sourceType;
      default = { };
      description = "Application-owned backup sources with independent paths and retention.";
    };
    paths = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "/srv/state" ];
      description = ''
        Deprecated aggregate path declaration retained for local-bootstrap
        compatibility. Off-host Restic jobs use sources.<name>.paths.
      '';
    };
  };

  config = lib.mkIf cfg.enable {
    assertions =
      (lib.concatMap (sourceName:
        let source = cfg.sources.${sourceName};
        in [
          {
            assertion = source.paths != [ ];
            message = "homecompute.backups.sources.${sourceName}.paths must not be empty";
          }
          {
            assertion = source.destinations != [ ];
            message = "homecompute.backups.sources.${sourceName}.destinations must name at least one destination";
          }
        ] ++ map (destinationName: {
          assertion = builtins.hasAttr destinationName cfg.destinations;
          message = "homecompute.backups.sources.${sourceName} references unknown destination ${destinationName}";
        }) source.destinations
      ) (builtins.attrNames cfg.sources))
      ++ (lib.concatMap (destinationName:
        let destination = cfg.destinations.${destinationName};
        in [
          {
            assertion = destination.repositoryBase != null
              && !(lib.hasInfix "uXXXXX" destination.repositoryBase);
            message = "homecompute.backups.destinations.${destinationName}.repositoryBase must contain the configured destination";
          }
          {
            assertion = destination.passwordFile != null;
            message = "homecompute.backups.destinations.${destinationName}.passwordFile must reference a runtime secret";
          }
          {
            assertion = destination.repositoryBase == null
              || !lib.hasPrefix "sftp:" destination.repositoryBase
              || (destination.sshPrivateKeyFile != null && destination.knownHostsFile != null);
            message = "SFTP backup destinations require a dedicated SSH key and pinned knownHostsFile";
          }
        ]
      ) (builtins.attrNames cfg.destinations));

    services.restic.backups = jobs;
    systemd.services = statusUnitConfig;
  };
}
