{ config, lib, pkgs, ... }:
let
  cfg = config.homecompute.androidGateway;
  python = pkgs.python3.withPackages (ps: [ ps.websockets ]);
  source = pkgs.runCommand "homecompute-android-gateway-source" { } ''
    mkdir -p $out/scripts
    cp ${../../scripts/android_notifications.py} $out/scripts/android_notifications.py
  '';
  gatewayConfig = pkgs.writeText "android-gateway.json" (builtins.toJSON {
    device_id = "android-tablet";
    base_url = "http://${cfg.portalHost}:${toString cfg.httpPort}";
    token_file = cfg.portalTokenFile;
  });
  notificationConfig = pkgs.writeText "android-notifications.json" (builtins.toJSON {
    device_id = "android-tablet";
    websocket_url = "ws://${cfg.portalHost}:${toString cfg.websocketPort}";
    allowed_packages = cfg.allowedPackages;
    webhook_url = cfg.webhookUrl;
  });
  external = value: lib.hasPrefix "/" value && !(lib.hasPrefix "/nix/store/" value);
in
{
  options.homecompute.androidGateway = {
    enable = lib.mkEnableOption "local Android Portal read-only gateway and notification listener";
    portalTokenFile = lib.mkOption { type = lib.types.str; default = "/run/secrets/android/portal-token"; };
    webhookTokenFile = lib.mkOption { type = lib.types.str; default = "/run/secrets/android/n8n-webhook-token"; };
    webhookUrl = lib.mkOption { type = lib.types.str; default = "http://127.0.0.1:15678/webhook/android-notifications"; };
    portalHost = lib.mkOption { type = lib.types.str; default = "android-tablet.home.arpa"; };
    httpPort = lib.mkOption { type = lib.types.port; default = 8080; };
    websocketPort = lib.mkOption { type = lib.types.port; default = 8081; };
    allowedPackages = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "com.facebook.orca" ];
      description = "Android application package IDs forwarded to n8n.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      { assertion = external cfg.portalTokenFile && external cfg.webhookTokenFile;
        message = "Android gateway configuration and credential paths must be external absolute paths."; }
      { assertion = builtins.match "[A-Za-z0-9.-]+" cfg.portalHost != null
          && lib.hasPrefix "http://127.0.0.1:" cfg.webhookUrl;
        message = "Android Portal must use a local host name and n8n webhook must stay on localhost."; }
      { assertion = cfg.allowedPackages != [] && lib.all (x: builtins.match "[A-Za-z0-9_.]+" x != null) cfg.allowedPackages;
        message = "Android gateway package allowlist must contain Android package IDs."; }
    ];
    users.groups.homecompute-android = { };
    users.users.homecompute-android = {
      isSystemUser = true;
      group = "homecompute-android";
      home = "/var/lib/homecompute-android-gateway";
    };
    environment.etc."homecompute/android-gateway.json" = {
      source = gatewayConfig;
      mode = "0444";
    };
    environment.etc."homecompute/android-notifications.json".source = notificationConfig;

    systemd.services.homecompute-android-notifications = {
      description = "Mobilerun Portal notification listener to authenticated n8n webhook";
      wantedBy = [ "multi-user.target" ];
      wants = [ "network-online.target" "docker.service" ];
      after = [ "network-online.target" "docker.service" ];
      restartTriggers = [ source ];
      environment = {
        ANDROID_CONFIG = "/etc/homecompute/android-notifications.json";
        PYTHONDONTWRITEBYTECODE = "1";
      };
      serviceConfig = {
        Type = "simple";
        User = "homecompute-android";
        Group = "homecompute-android";
        UMask = "0077";
        LoadCredential = [
          "portal-token:${cfg.portalTokenFile}"
          "n8n-webhook-token:${cfg.webhookTokenFile}"
        ];
        ExecStart = "${python}/bin/python ${source}/scripts/android_notifications.py";
        Restart = "always";
        RestartSec = "2s";
        StateDirectory = "homecompute-android-gateway";
        StateDirectoryMode = "0700";
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        PrivateDevices = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" ];
        CapabilityBoundingSet = "";
        MemoryMax = "256M";
        TasksMax = 64;
        TimeoutStopSec = "10s";
      };
    };
  };
}
