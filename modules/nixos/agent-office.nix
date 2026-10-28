{ config, lib, pkgs, ... }:
let
  cfg = config.homecompute.agentOffice;
  rule = "-i br-hc-ctrl -s 172.28.200.2/32 -d 172.28.200.1/32 -p tcp --dport 18802 -m conntrack --ctstate NEW,ESTABLISHED -m comment --comment hc-agent-office -j ACCEPT";
in
{
  options.homecompute.agentOffice.enable = lib.mkEnableOption "private bridge ingress for the authenticated OpenClaw agent office";
  config = lib.mkIf cfg.enable {
    assertions = [{
      assertion = config.homecompute.agentsVm.enable;
      message = "Agent office ingress requires the isolated agents VM";
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
    systemd.services.homecompute-agent-office-proxy = {
      description = "Caddy bridge proxy for the authenticated OpenClaw agent office";
      wantedBy = [ "multi-user.target" ];
      wants = [ "docker.service" "homecompute-agents-vm.service" ];
      requires = [ "homecompute-agent-office-firewall.service" ];
      after = [ "docker.service" "homecompute-agents-vm.service" "homecompute-agent-office-firewall.service" ];
      unitConfig.StartLimitIntervalSec = 0;
      serviceConfig = {
        ExecStart = "${pkgs.socat}/bin/socat TCP4-LISTEN:18802,bind=172.28.200.1,reuseaddr,fork TCP4:10.77.20.2:18802";
        Restart = "on-failure";
        RestartSec = "5s";
        NoNewPrivileges = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        PrivateTmp = true;
        CapabilityBoundingSet = "";
      };
    };
    # A separately buildable unit permits the workload's guarded narrow rollout
    # without switching unrelated NixOS or Compose applications. The main
    # firewall declaration above also restores the rule after a firewall reload.
    systemd.services.homecompute-agent-office-firewall = {
      description = "Caddy-only admission for the agent office bridge";
      after = [ "firewall.service" "docker.service" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        ExecStart = pkgs.writeShellScript "agent-office-firewall-start" ''
          ${pkgs.iptables}/bin/iptables -w -C INPUT ${rule} 2>/dev/null || ${pkgs.iptables}/bin/iptables -w -I INPUT 1 ${rule}
        '';
        ExecStop = pkgs.writeShellScript "agent-office-firewall-stop" ''
          while ${pkgs.iptables}/bin/iptables -w -C INPUT ${rule} 2>/dev/null; do
            ${pkgs.iptables}/bin/iptables -w -D INPUT ${rule}
          done
        '';
      };
    };
  };
}
