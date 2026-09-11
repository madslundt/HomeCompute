{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homecompute.ttlockWebhook;
  composeFile = "${cfg.repositoryPath}/deploy/ttlock-webhook/compose.yaml";
  compose = "${pkgs.docker}/bin/docker compose --env-file ${cfg.environmentFile} -f ${composeFile}";
in
{
  options.homecompute.ttlockWebhook = {
    enable = lib.mkEnableOption "the TTLock webhook gateway and its exclusive Funnel";
    repositoryPath = lib.mkOption {
      type = lib.types.str;
      default = "/home/mads/HomeCompute";
      description = "Absolute checkout containing the TTLock Compose project.";
    };
    environmentFile = lib.mkOption {
      type = lib.types.str;
      default = "/run/secrets/ttlock-webhook/environment";
      description = "SOPS-rendered dotenv file containing both independent webhook secrets.";
    };
    gatewayPort = lib.mkOption {
      type = lib.types.port;
      default = 8085;
      description = "Loopback-only port proxied by Tailscale Funnel.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = config.homecompute.secrets.enable;
        message = "TTLock webhook deployment requires homecompute.secrets.enable";
      }
      {
        assertion = lib.hasPrefix "/" cfg.repositoryPath && lib.hasPrefix "/run/secrets/" cfg.environmentFile;
        message = "TTLock paths must be absolute and the environment must come from /run/secrets";
      }
    ];

    sops.secrets."ttlock-webhook/environment" = {
      mode = "0400";
      restartUnits = [ "ttlock-webhook.service" ];
    };

    systemd.services.ttlock-webhook = {
      description = "TTLock webhook gateway Compose project";
      wantedBy = [ "multi-user.target" ];
      after = [ "docker.service" "network-online.target" ];
      wants = [ "docker.service" "network-online.target" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        ExecStart = pkgs.writeShellScript "start-ttlock-webhook" ''
          set -Eeuo pipefail
          ${compose} up -d --build --remove-orphans --wait --wait-timeout 60
        '';
        ExecReload = pkgs.writeShellScript "reload-ttlock-webhook" ''
          set -Eeuo pipefail
          ${compose} up -d --build --remove-orphans --wait --wait-timeout 60
        '';
        ExecStop = "${compose} stop";
        TimeoutStartSec = 300;
        TimeoutStopSec = 60;
      };
    };

    systemd.services.ttlock-funnel = {
      description = "Exclusive public Funnel for the TTLock webhook gateway";
      wantedBy = [ "multi-user.target" ];
      after = [ "tailscaled.service" "ttlock-webhook.service" ];
      requires = [ "tailscaled.service" "ttlock-webhook.service" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        ExecStart = "${pkgs.tailscale}/bin/tailscale funnel --yes --bg --https=443 http://127.0.0.1:${toString cfg.gatewayPort}";
        ExecReload = "${pkgs.tailscale}/bin/tailscale funnel --yes --bg --https=443 http://127.0.0.1:${toString cfg.gatewayPort}";
        ExecStop = "${pkgs.tailscale}/bin/tailscale funnel --https=443 off";
        TimeoutStartSec = 60;
      };
    };
  };
}
