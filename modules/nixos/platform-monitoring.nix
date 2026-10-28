{ pkgs, ... }:
let
  metricDirectory = "/var/lib/homecompute-health";

in
{
  systemd.tmpfiles.rules = [
    "d ${metricDirectory} 0755 root root - -"
  ];

  services.prometheus.exporters.node = {
    enable = true;
    listenAddress = "127.0.0.1";
    port = 9100;
    openFirewall = false;
    enabledCollectors = [
      "systemd"
      "textfile"
    ];
    extraFlags = [
      "--collector.textfile.directory=${metricDirectory}"
    ];
  };

  systemd.services.homecompute-platform-health = {
    description = "Probe HomeCompute platform health metadata";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    path = [ pkgs.curl pkgs.docker ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.python3}/bin/python3 ${../../scripts/platform-health.py}";
      TimeoutStartSec = "20s";
      RuntimeDirectory = "homecompute-platform-health";
      RuntimeDirectoryMode = "0700";
      UMask = "0022";

      AmbientCapabilities = "";
      CapabilityBoundingSet = "";
      DevicePolicy = "closed";
      IPAddressDeny = "any";
      IPAddressAllow = [
        "127.0.0.1/8"
        "100.110.248.102/32"
        # cgroup IP filtering observes Docker's post-DNAT destination rather
        # than the loopback/Tailscale address used by curl.
        "172.28.200.0/24"
        "172.28.201.0/24"
      ];
      LockPersonality = true;
      MemoryDenyWriteExecute = true;
      MemoryMax = "128M";
      NoNewPrivileges = true;
      PrivateDevices = true;
      PrivateTmp = true;
      ProtectClock = true;
      ProtectControlGroups = true;
      ProtectHome = true;
      ProtectHostname = true;
      ProtectKernelLogs = true;
      ReadOnlyPaths = [ "-/srv/state/control-plane/caddy-data/caddy/pki/authorities/local/root.crt" ];
      ProtectKernelTunables = true;
      ProtectProc = "invisible";
      ProtectSystem = "strict";
      ReadWritePaths = [ metricDirectory ];
      RestrictAddressFamilies = [
        "AF_UNIX"
        "AF_INET"
        "AF_INET6"
      ];
      RestrictNamespaces = true;
      RestrictRealtime = true;
      RestrictSUIDSGID = true;
      SystemCallArchitectures = "native";
      SystemCallFilter = [
        "@system-service"
        "~@privileged"
      ];
    };
  };

  systemd.timers.homecompute-platform-health = {
    description = "Periodically probe HomeCompute platform health";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "90s";
      OnUnitActiveSec = "60s";
      AccuracySec = "5s";
      RandomizedDelaySec = "10s";
      Unit = "homecompute-platform-health.service";
    };
  };
}
