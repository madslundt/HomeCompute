{ config, lib, ... }:
let
  cfg = config.homecompute.immich;
  environmentTemplate = builtins.readFile ../../config/immich.env.example;
in
{
  options.homecompute.immich = {
    enable = lib.mkEnableOption "Immich photo and video library";
    importEnabled = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Enable Google Takeout import tooling after an Immich API key has been provisioned.";
    };
    stateRoot = lib.mkOption {
      type = lib.types.str;
      default = "/srv/state/immich";
      description = "Local root for Immich database, caches, dumps, and imports.";
    };
    libraryPath = lib.mkOption {
      type = lib.types.str;
      default = "/srv/state/immich/library";
      description = "Host path for Immich originals; can move to a mounted NAS independently.";
    };
    lanAddress = lib.mkOption {
      type = lib.types.str;
      default = "192.168.30.122";
      description = "Explicit LAN bind address.";
    };
    tailscaleAddress = lib.mkOption {
      type = lib.types.str;
      default = "100.110.248.102";
      description = "Explicit Tailscale bind address.";
    };
    port = lib.mkOption {
      type = lib.types.port;
      default = 2283;
      description = "Immich web/API port.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = lib.hasPrefix "/" cfg.stateRoot && lib.hasPrefix "/" cfg.libraryPath;
        message = "Immich stateRoot and libraryPath must be absolute host paths";
      }
      {
        assertion = cfg.libraryPath != "${cfg.stateRoot}/database";
        message = "Immich libraryPath must remain separate from its local PostgreSQL data";
      }
    ];

    systemd.tmpfiles.rules = lib.optionals (cfg.stateRoot != "/srv/state/immich") ([
      "d ${cfg.stateRoot} 0750 root homecompute-state - -"
      "d ${cfg.stateRoot}/database 0700 999 999 - -"
      "d ${cfg.stateRoot}/model-cache 0750 1000 1000 - -"
      "d ${cfg.stateRoot}/redis 0750 999 999 - -"
      "d ${cfg.stateRoot}/db-backups 0700 root root - -"
      "d ${cfg.stateRoot}/import 0700 root root - -"
    ] ++ lib.optional (cfg.libraryPath != "${cfg.stateRoot}/library")
      "d ${cfg.libraryPath} 0750 root homecompute-state - -")
      ++ lib.optional (cfg.stateRoot == "/srv/state/immich" && cfg.libraryPath != "${cfg.stateRoot}/library")
        "d ${cfg.libraryPath} 0750 root homecompute-state - -";

    environment.etc."homecompute/immich.env" = {
      mode = "0600";
      text = lib.replaceStrings
        [
          "IMMICH_STATE_ROOT=/srv/state/immich"
          "IMMICH_LIBRARY_PATH=/srv/state/immich/library"
          "IMMICH_DATABASE_PATH=/srv/state/immich/database"
          "IMMICH_MODEL_CACHE_PATH=/srv/state/immich/model-cache"
          "IMMICH_REDIS_PATH=/srv/state/immich/redis"
          "IMMICH_DB_BACKUPS_PATH=/srv/state/immich/db-backups"
          "IMMICH_IMPORT_PATH=/srv/state/immich/import"
          "IMMICH_LAN_ADDRESS=192.168.30.122"
          "IMMICH_TAILSCALE_ADDRESS=100.110.248.102"
          "IMMICH_PORT=2283"
        ]
        [
          "IMMICH_STATE_ROOT=${cfg.stateRoot}"
          "IMMICH_LIBRARY_PATH=${cfg.libraryPath}"
          "IMMICH_DATABASE_PATH=${cfg.stateRoot}/database"
          "IMMICH_MODEL_CACHE_PATH=${cfg.stateRoot}/model-cache"
          "IMMICH_REDIS_PATH=${cfg.stateRoot}/redis"
          "IMMICH_DB_BACKUPS_PATH=${cfg.stateRoot}/db-backups"
          "IMMICH_IMPORT_PATH=${cfg.stateRoot}/import"
          "IMMICH_LAN_ADDRESS=${cfg.lanAddress}"
          "IMMICH_TAILSCALE_ADDRESS=${cfg.tailscaleAddress}"
          "IMMICH_PORT=${toString cfg.port}"
        ]
        environmentTemplate;
    };
  };
}
