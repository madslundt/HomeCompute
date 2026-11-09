{ config, lib, pkgs, ... }:
let
  cfg = config.homecompute.opencode;
in
{
  options.homecompute.opencode = {
    enable = lib.mkEnableOption "private OpenCode web server";

    projectsDirectory = lib.mkOption {
      type = lib.types.str;
      default = "/srv/state/opencode/projects";
      description = "The only writable project tree exposed to the OpenCode service.";
    };

    stateDirectory = lib.mkOption {
      type = lib.types.str;
      default = "/srv/state/opencode";
      description = "Persistent OpenCode sessions and provider authentication.";
    };

    port = lib.mkOption {
      type = lib.types.port;
      default = 4096;
      description = "Loopback-only HTTP listener for OpenCode web.";
    };

    httpsPort = lib.mkOption {
      type = lib.types.port;
      default = 18793;
      description = "Tailnet-only HTTPS port published through Tailscale Serve.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.projectsDirectory == "/srv/state/opencode/projects";
        message = "OpenCode is restricted to /srv/state/opencode/projects; change this only after reviewing its filesystem boundary.";
      }
      {
        assertion = cfg.stateDirectory == "/srv/state/opencode";
        message = "OpenCode state must remain under /srv/state/opencode.";
      }
      {
        assertion = cfg.port != cfg.httpsPort;
        message = "OpenCode's loopback and Tailscale Serve ports must differ.";
      }
    ];

    environment.systemPackages = [ pkgs.opencode ];
    environment.etc."opencode.json".source = ../../config/opencode.json;

    systemd.tmpfiles.rules = [
      "d ${cfg.stateDirectory} 0700 agent users - -"
      "d ${cfg.projectsDirectory} 0700 agent users - -"
    ];

    systemd.services.homecompute-opencode = {
      description = "Private OpenCode coding workspace";
      wantedBy = [ "multi-user.target" ];
      wants = [ "network-online.target" ];
      after = [ "network-online.target" "sops-nix.service" ];
      unitConfig.ConditionPathExists = "${cfg.stateDirectory}/litellm.env";
      path = with pkgs; [ bash coreutils curl git jq nodejs python3 ripgrep ];
      serviceConfig = {
        Type = "simple";
        User = "agent";
        Group = "users";
        WorkingDirectory = "/run/homecompute-opencode/projects";
        Slice = "user-1001.slice";
        RuntimeDirectory = "homecompute-opencode";
        RuntimeDirectoryMode = "0700";
        Environment = [
          "HOME=/run/homecompute-opencode"
          "XDG_CONFIG_HOME=/run/homecompute-opencode/config"
          "XDG_DATA_HOME=/run/homecompute-opencode/data"
          "XDG_STATE_HOME=/run/homecompute-opencode/state"
          "XDG_CACHE_HOME=/run/homecompute-opencode/cache"
          "OPENCODE_CONFIG=/etc/opencode.json"
          "OPENCODE_DISABLE_PROJECT_CONFIG=true"
        ];
        EnvironmentFile = [
          config.sops.secrets."opencode/server-password".path
          "${cfg.stateDirectory}/litellm.env"
        ];
        ExecStart = "${pkgs.opencode}/bin/opencode serve --hostname 127.0.0.1 --port ${toString cfg.port}";
        Restart = "on-failure";
        RestartSec = "5s";
        UMask = "0077";
        NoNewPrivileges = true;
        CapabilityBoundingSet = "";
        AmbientCapabilities = "";
        PrivateTmp = true;
        PrivateDevices = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        BindReadWritePaths = [ "${cfg.stateDirectory}:/run/homecompute-opencode" ];
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        RestrictSUIDSGID = true;
        RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" "AF_INET6" ];
        LockPersonality = true;
        SystemCallArchitectures = "native";
      };
    };

    systemd.services.homecompute-opencode-serve = {
      description = "Private Tailscale HTTPS access to OpenCode";
      wantedBy = [ "multi-user.target" ];
      wants = [ "tailscaled.service" "homecompute-opencode.service" ];
      after = [ "tailscaled.service" "homecompute-opencode.service" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        ExecStart = "${pkgs.tailscale}/bin/tailscale serve --bg --yes --https=${toString cfg.httpsPort} http://127.0.0.1:${toString cfg.port}";
        ExecStop = "${pkgs.tailscale}/bin/tailscale serve --https=${toString cfg.httpsPort} off";
      };
    };
  };
}
