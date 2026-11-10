{ config, lib, pkgs, ... }:
let
  cfg = config.homecompute.hermesDashboard;
  rule = "-i br-hc-ctrl -s 172.28.200.2/32 -d 172.28.200.1/32 -p tcp --dport 18805 -m conntrack --ctstate NEW,ESTABLISHED -m comment --comment hc-hermes-dashboard -j ACCEPT";
in
{
  options.homecompute.hermesDashboard.enable = lib.mkEnableOption "private bridge ingress for the authenticated Hermes dashboard";
  config = lib.mkIf cfg.enable {
    assertions = [{
      assertion = config.homecompute.agentsVm.enable;
      message = "Hermes dashboard ingress requires the isolated agents VM";
    }];
    # Caddy is the only permitted host-bridge client. No new LAN/Tailscale port.
    networking.firewall.extraCommands = lib.mkAfter ''
      iptables -w -C INPUT ${rule} 2>/dev/null || iptables -w -I INPUT 1 ${rule}
    '';
    networking.firewall.extraStopCommands = lib.mkAfter ''
      while iptables -w -C INPUT ${rule} 2>/dev/null; do
        iptables -w -D INPUT ${rule}
      done
    '';
    systemd.services.homecompute-hermes-dashboard-proxy = {
      description = "Caddy bridge proxy for the authenticated Hermes dashboard";
      wantedBy = [ "multi-user.target" ];
      wants = [ "docker.service" "homecompute-agents-vm.service" ];
      requires = [ "homecompute-hermes-dashboard-firewall.service" ];
      after = [ "docker.service" "homecompute-agents-vm.service" "homecompute-hermes-dashboard-firewall.service" ];
      unitConfig.StartLimitIntervalSec = 0;
      serviceConfig = {
        ExecStart = "${pkgs.socat}/bin/socat TCP4-LISTEN:18805,bind=172.28.200.1,reuseaddr,fork TCP4:10.77.20.2:18804";
        Restart = "on-failure";
        RestartSec = "5s";
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        CapabilityBoundingSet = "";
      };
    };
    # Desktop connects to Hermes through Tailscale Serve. This routes around
    # the shared Docker-published Caddy listener, which rejects the tailnet
    # request before Hermes auth, while retaining the Webapp's native
    # authentication and the guest relay's byte-for-byte HTTP/WebSocket path.
    systemd.services.homecompute-hermes-dashboard-serve = {
      description = "Private Tailscale HTTPS access to the authenticated Hermes dashboard";
      wantedBy = [ "multi-user.target" ];
      wants = [ "tailscaled.service" "homecompute-agents-vm.service" ];
      after = [ "tailscaled.service" "homecompute-agents-vm.service" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        ExecStart = "${pkgs.tailscale}/bin/tailscale serve --bg --yes --https=18796 http://10.77.20.2:18804";
        ExecStop = "${pkgs.tailscale}/bin/tailscale serve --https=18796 off";
      };
    };
    # A separately buildable unit permits the workload's guarded narrow rollout
    # without switching unrelated NixOS or Compose applications. The main
    # firewall declaration above also restores the rule after a firewall reload.
    systemd.services.homecompute-hermes-dashboard-firewall = {
      description = "Caddy-only admission for the Hermes dashboard bridge";
      after = [ "firewall.service" "docker.service" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        ExecStart = pkgs.writeShellScript "hermes-dashboard-firewall-start" ''
          ${pkgs.iptables}/bin/iptables -w -C INPUT ${rule} 2>/dev/null || ${pkgs.iptables}/bin/iptables -w -I INPUT 1 ${rule}
        '';
        ExecStop = pkgs.writeShellScript "hermes-dashboard-firewall-stop" ''
          while ${pkgs.iptables}/bin/iptables -w -C INPUT ${rule} 2>/dev/null; do
            ${pkgs.iptables}/bin/iptables -w -D INPUT ${rule}
          done
        '';
      };
    };
  };
}
