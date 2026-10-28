{ config, lib, pkgs, ... }:
let
  cfg = config.homecompute.serviceRecovery;
in {
  options.homecompute.serviceRecovery.enable = lib.mkEnableOption
    "bounded recovery of existing containers after boot-time network failures";
  config = lib.mkIf cfg.enable {
    systemd.services.homecompute-service-recovery = {
      description = "Recover existing HomeCompute services after network readiness";
      wants = [ "network-online.target" "docker.service" ];
      after = [ "network-online.target" "docker.service" ];
      path = [ pkgs.docker pkgs.util-linux pkgs.iproute2 pkgs.systemd ];
      environment.DOCKER_HOST = "unix:///var/run/docker.sock";
      serviceConfig = {
        Type = "oneshot";
        RuntimeDirectory = "homecompute-service-recovery";
        RuntimeDirectoryMode = "0700";
        RuntimeDirectoryPreserve = true;
        ExecStart = "${pkgs.python3}/bin/python3 ${../../scripts/home-core-service-recovery.py} repair";
        TimeoutStartSec = 120;
        UMask = "0077";
        NoNewPrivileges = true;
        PrivateTmp = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        UnsetEnvironment = [ "DOCKER_CONTEXT" "DOCKER_TLS_VERIFY" "DOCKER_CERT_PATH" ];
      };
    };
    systemd.timers.homecompute-service-recovery = {
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnBootSec = "60s";
        OnUnitInactiveSec = "120s";
      };
    };
  };
}
