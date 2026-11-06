{ config, lib, pkgs, ... }:
let
  cfg = config.homecompute.assistantContext;
  source = pkgs.runCommand "homecompute-assistant-context-source" { } ''
    mkdir -p $out/scripts $out/config
    ${lib.concatMapStringsSep "\n" (name: "cp ${../../scripts + "/${name}"} $out/scripts/${name}") [
      "assistant-context-server.py"
      "assistant_context_api.py"
      "assistant_context_store.py"
      "assistant_context_tools.py"
      "assistant_attention.py"
      "assistant_context_delivery.py"
    ]}
    cp ${../../config/assistant-attention.json} $out/config/assistant-attention.json
  '';
in
{
  options.homecompute.assistantContext = {
    enable = lib.mkEnableOption "private proactive-assistant context (initially shadow only)";
    credentialsFile = lib.mkOption {
      type = lib.types.str;
      default = "/etc/homecompute/assistant-context-clients.json";
      description = "External root-only client credentials, copied with systemd LoadCredential; never a Nix-store secret.";
    };
    dataClassification = lib.mkOption {
      type = lib.types.enum [ "synthetic-only" "household" ];
      default = "synthetic-only";
    };
    restoreVerified = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "An actual isolated restore of this service's state and credentials has been verified.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = lib.hasPrefix "/" cfg.credentialsFile && !(lib.hasPrefix "/nix/store/" cfg.credentialsFile);
        message = "Assistant context credentials must be an external absolute path, never a Nix-store value.";
      }
      {
        assertion = cfg.dataClassification == "synthetic-only" ||
          (cfg.restoreVerified && config.homecompute.nasRecoveryBackup.enable);
        message = "Household context requires off-host state coverage and an isolated restore; the NAS marker alone is insufficient.";
      }
    ];
    users.groups.homecompute-context = { };
    users.users.homecompute-context = {
      isSystemUser = true;
      group = "homecompute-context";
      home = "/srv/state/assistant-context";
    };
    systemd.tmpfiles.rules = [
      "d /srv/state/assistant-context 0700 homecompute-context homecompute-context - -"
    ];
    systemd.services.homecompute-assistant-context = {
      description = "Private source-linked assistant context and shadow attention";
      wantedBy = [ "multi-user.target" ];
      after = [ "network.target" ];
      unitConfig.RequiresMountsFor = [ "/srv/state/assistant-context" ];
      environment.PYTHONDONTWRITEBYTECODE = "1";
      serviceConfig = {
        User = "homecompute-context";
        Group = "homecompute-context";
        UMask = "0077";
        LoadCredential = [ "clients:${cfg.credentialsFile}" ];
        ExecStart = "${pkgs.python3}/bin/python3 ${source}/scripts/assistant-context-server.py --state /srv/state/assistant-context --clients %d/clients --policy ${source}/config/assistant-attention.json";
        Restart = "on-failure";
        RestartSec = 5;
        NoNewPrivileges = true;
        CapabilityBoundingSet = "";
        PrivateTmp = true;
        PrivateDevices = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        RestrictSUIDSGID = true;
        RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" ];
        IPAddressDeny = "any";
        IPAddressAllow = "127.0.0.1/32";
        ReadWritePaths = [ "/srv/state/assistant-context" ];
        MemoryMax = "256M";
        TasksMax = 32;
      };
    };
  };
}
