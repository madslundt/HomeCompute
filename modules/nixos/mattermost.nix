{ config, lib, pkgs, ... }:
let
  cfg = config.homecompute.mattermost;
  prefix = "${pkgs.python3}/bin/python3 ${cfg.installRoot}/setup-mattermost.py --state-root ${cfg.stateRoot} --install-root ${cfg.installRoot}";
in
{
  options.homecompute.mattermost = {
    enable = lib.mkEnableOption "the private Mattermost Team Edition chat service";
    stateRoot = lib.mkOption {
      type = lib.types.str;
      default = "/srv/state/mattermost";
      description = "Private persistent state initialized by setup-mattermost.py.";
    };
    installRoot = lib.mkOption {
      type = lib.types.str;
      default = "/opt/homecompute/mattermost";
      description = "Root-owned reviewed Compose and lifecycle installation.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [{
      assertion = builtins.match "/[a-zA-Z0-9_./-]+" cfg.stateRoot != null
        && builtins.match "/[a-zA-Z0-9_./-]+" cfg.installRoot != null;
      message = "Mattermost managed paths must be absolute without whitespace";
    }];
    networking.firewall.interfaces.tailscale0.allowedTCPPorts = [ 9443 ];
    # Only the native agent and the existing LAN HTTPS proxy may reach this
    # exact tailnet listener through their respective isolated bridges.
    networking.firewall.extraCommands = lib.mkAfter ''
      iptables -w -C INPUT -i br-hc-agents -s 10.77.20.2/32 -d 100.110.248.102/32 \
        -p tcp --dport 9443 -m conntrack --ctstate NEW,ESTABLISHED \
        -m comment --comment homecompute-mattermost-private -j ACCEPT 2>/dev/null || \
      iptables -w -I INPUT 1 -i br-hc-agents -s 10.77.20.2/32 -d 100.110.248.102/32 \
        -p tcp --dport 9443 -m conntrack --ctstate NEW,ESTABLISHED \
        -m comment --comment homecompute-mattermost-private -j ACCEPT
      iptables -w -C INPUT -i br-hc-ctrl -s 172.28.200.2/32 -d 100.110.248.102/32 \
        -p tcp --dport 9443 -m conntrack --ctstate NEW,ESTABLISHED \
        -m comment --comment homecompute-mattermost-private -j ACCEPT 2>/dev/null || \
      iptables -w -I INPUT 1 -i br-hc-ctrl -s 172.28.200.2/32 -d 100.110.248.102/32 \
        -p tcp --dport 9443 -m conntrack --ctstate NEW,ESTABLISHED \
        -m comment --comment homecompute-mattermost-private -j ACCEPT
    '';
    networking.firewall.extraStopCommands = lib.mkAfter ''
      while iptables -w -C INPUT -i br-hc-agents -s 10.77.20.2/32 -d 100.110.248.102/32 \
        -p tcp --dport 9443 -m conntrack --ctstate NEW,ESTABLISHED \
        -m comment --comment homecompute-mattermost-private -j ACCEPT 2>/dev/null; do
        iptables -w -D INPUT -i br-hc-agents -s 10.77.20.2/32 -d 100.110.248.102/32 \
          -p tcp --dport 9443 -m conntrack --ctstate NEW,ESTABLISHED \
          -m comment --comment homecompute-mattermost-private -j ACCEPT
      done
      while iptables -w -C INPUT -i br-hc-ctrl -s 172.28.200.2/32 -d 100.110.248.102/32 \
        -p tcp --dport 9443 -m conntrack --ctstate NEW,ESTABLISHED \
        -m comment --comment homecompute-mattermost-private -j ACCEPT 2>/dev/null; do
        iptables -w -D INPUT -i br-hc-ctrl -s 172.28.200.2/32 -d 100.110.248.102/32 \
          -p tcp --dport 9443 -m conntrack --ctstate NEW,ESTABLISHED \
          -m comment --comment homecompute-mattermost-private -j ACCEPT
      done
    '';
    systemd.services.homecompute-mattermost = {
      description = "HomeCompute Mattermost Team Edition";
      wantedBy = [ "multi-user.target" ];
      after = [ "docker.service" "network-online.target" "tailscaled.service" "homecompute-gateway-certificate.service" ];
      wants = [ "docker.service" "network-online.target" "tailscaled.service" ];
      path = [ pkgs.docker pkgs.iptables ];
      unitConfig.StartLimitIntervalSec = 0;
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        ExecStart = "${prefix} start";
        ExecReload = "${prefix} start";
        ExecStop = "${prefix} stop";
        Restart = "on-failure";
        RestartSec = "20s";
        TimeoutStartSec = "600s";
        TimeoutStopSec = "90s";
        UMask = "0077";
        NoNewPrivileges = true;
        PrivateTmp = true;
      };
    };
    systemd.services.homecompute-mattermost-certificate = {
      description = "Reload Mattermost proxy after public certificate renewal";
      after = [ "homecompute-mattermost.service" "homecompute-gateway-certificate.service" ];
      path = [ pkgs.docker ];
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "${prefix} reload-proxy";
        UMask = "0077";
      };
    };
    systemd.timers.homecompute-mattermost-certificate = {
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnCalendar = "daily";
        RandomizedDelaySec = "2h";
        Persistent = true;
      };
    };
    systemd.services.homecompute-mattermost-atlas-membership = {
      description = "Join Atlas to active owner HomeCompute channels";
      after = [ "homecompute-mattermost.service" ];
      unitConfig.ConditionPathExists = [ "${cfg.stateRoot}/private/agent-account-state.json" "${cfg.stateRoot}/private/n8n-channels.json" ];
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "${pkgs.python3}/bin/python3 ${cfg.installRoot}/sync-mattermost-atlas.py --private-root ${cfg.stateRoot}/private";
        Environment = "PYTHONDONTWRITEBYTECODE=1";
        TimeoutStartSec = "45s";
        UMask = "0077";
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        PrivateDevices = true;
        ReadWritePaths = [ "${cfg.stateRoot}/private" ];
        CapabilityBoundingSet = "";
        RestrictAddressFamilies = [ "AF_INET" "AF_UNIX" ];
      };
    };
    systemd.timers.homecompute-mattermost-atlas-membership = {
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnBootSec = "30s";
        OnUnitInactiveSec = "30s";
        AccuracySec = "1s";
        Unit = "homecompute-mattermost-atlas-membership.service";
      };
    };
  };
}
