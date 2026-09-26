{ config, lib, pkgs, ... }:
let
  cfg = config.homecompute.computeSshTunnel;
  tunnelRunner = pkgs.writeShellApplication {
    name = "homecompute-compute-ssh-tunnel";
    runtimeInputs = [ pkgs.coreutils pkgs.iproute2 pkgs.openssh ];
    text = ''
      set -Eeuo pipefail

      identity='${cfg.identityFile}'
      known_hosts='${cfg.knownHostsFile}'
      target='${cfg.sshUser}@${cfg.sshHost}'
      bind_address='172.28.200.1'

      [ -f "$identity" ] || { echo "missing compute tunnel identity: $identity" >&2; exit 1; }
      [ "$(stat -c '%U:%G:%a' "$identity")" = root:root:400 ] || {
        echo "compute tunnel identity must be root:root mode 0400" >&2
        exit 1
      }
      [ -f "$known_hosts" ] || { echo "missing pinned compute known_hosts: $known_hosts" >&2; exit 1; }
      [ "$(stat -c '%U:%G' "$known_hosts")" = root:root ] || {
        echo "compute known_hosts must be owned by root:root" >&2
        exit 1
      }
      [ $((8#$(stat -c '%a' "$known_hosts") & 8#022)) -eq 0 ] || {
        echo "compute known_hosts must not be group/world writable" >&2
        exit 1
      }
      ssh-keygen -F '${cfg.sshHost}' -f "$known_hosts" >/dev/null || {
        echo "compute known_hosts has no pinned key for ${cfg.sshHost}" >&2
        exit 1
      }

      # The Compose bridge is created by the control-plane project. Restarting
      # this unit is harmless; wait here so ssh never broadens its bind when the
      # bridge has not been created yet.
      for attempt in $(seq 1 60); do
        ip -4 address show | grep -Fq " $bind_address/" && break
        [ "$attempt" -lt 60 ] || {
          echo "control-plane bridge gateway $bind_address is unavailable" >&2
          exit 1
        }
        sleep 1
      done

      exec ssh -F /dev/null -N -T \
        -o BatchMode=yes \
        -o ConnectTimeout=10 \
        -o ExitOnForwardFailure=yes \
        -o GlobalKnownHostsFile=/dev/null \
        -o IdentitiesOnly=yes \
        -o IdentityAgent=none \
        -o KbdInteractiveAuthentication=no \
        -o PasswordAuthentication=no \
        -o PreferredAuthentications=publickey \
        -o PermitLocalCommand=no \
        -o RequestTTY=no \
        -o ServerAliveCountMax=3 \
        -o ServerAliveInterval=15 \
        -o StrictHostKeyChecking=yes \
        -o TCPKeepAlive=no \
        -o UserKnownHostsFile="$known_hosts" \
        -i "$identity" \
        -p '${toString cfg.sshPort}' \
        -L "$bind_address:18005:127.0.0.1:8005" \
        -L "$bind_address:18006:127.0.0.1:8006" \
        -L "$bind_address:18201:127.0.0.1:10201" \
        -L "$bind_address:18301:127.0.0.1:10301" \
        "$target"
    '';
  };
in
{
  options.homecompute.computeSshTunnel = {
    enable = lib.mkEnableOption "explicit home-core to home-spark SSH inference fallback";

    sshHost = lib.mkOption {
      type = lib.types.str;
      default = "192.168.30.126";
      description = "Trusted-management address of home-spark.";
    };

    sshPort = lib.mkOption {
      type = lib.types.port;
      default = 22;
      description = "OpenSSH port on home-spark.";
    };

    sshUser = lib.mkOption {
      type = lib.types.str;
      default = "homecompute-tunnel";
      description = "Dedicated unprivileged forwarding-only account on home-spark.";
    };

    identityFile = lib.mkOption {
      type = lib.types.str;
      default = "/etc/homecompute/compute-tunnel/id_ed25519";
      description = "Root-owned mode-0400 private key installed outside the Nix store.";
    };

    knownHostsFile = lib.mkOption {
      type = lib.types.str;
      default = "/etc/homecompute/compute-tunnel/known_hosts";
      description = "Root-owned file containing the reviewed home-spark SSH host key.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.sshHost == "192.168.30.126";
        message = "compute SSH fallback is qualified only for home-spark at 192.168.30.126";
      }
      {
        assertion = cfg.sshUser == "homecompute-tunnel";
        message = "compute SSH fallback must use the dedicated homecompute-tunnel account";
      }
      {
        assertion = cfg.sshPort == 22;
        message = "compute SSH fallback is qualified only for home-spark SSH port 22";
      }
      {
        assertion = cfg.identityFile == "/etc/homecompute/compute-tunnel/id_ed25519";
        message = "compute SSH fallback must use its dedicated root-owned identity path";
      }
      {
        assertion = cfg.knownHostsFile == "/etc/homecompute/compute-tunnel/known_hosts";
        message = "compute SSH fallback must use its dedicated pinned known_hosts path";
      }
    ];

    systemd.tmpfiles.rules = [
      "d /etc/homecompute/compute-tunnel 0700 root root -"
    ];

    systemd.services.homecompute-compute-ssh-tunnel-firewall = {
      description = "Fail-closed INPUT policy for the home-spark SSH forwards";
      wantedBy = [ "multi-user.target" ];
      requiredBy = [ "homecompute-compute-ssh-tunnel.service" ];
      before = [ "homecompute-compute-ssh-tunnel.service" ];
      path = [ pkgs.iptables ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
      };
      script = ''
        # Install a temporary reject before replacing the live chain so a
        # failed activation cannot expose a forward to another edge container.
        iptables -w -I INPUT 1 -d 172.28.200.1/32 -p tcp -m multiport --dports 18005,18006,18201,18301 -j REJECT
        while iptables -w -C INPUT -d 172.28.200.1/32 -p tcp -m multiport --dports 18005,18006,18201,18301 -j HC-COMPUTE-TUNNEL 2>/dev/null; do
          iptables -w -D INPUT -d 172.28.200.1/32 -p tcp -m multiport --dports 18005,18006,18201,18301 -j HC-COMPUTE-TUNNEL
        done
        iptables -w -N HC-COMPUTE-TUNNEL 2>/dev/null || true
        iptables -w -F HC-COMPUTE-TUNNEL
        iptables -w -A HC-COMPUTE-TUNNEL -s 172.28.200.3/32 -d 172.28.200.1/32 -p tcp --dport 18005 -m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT
        iptables -w -A HC-COMPUTE-TUNNEL -s 172.28.200.3/32 -d 172.28.200.1/32 -p tcp --dport 18006 -m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT
        iptables -w -A HC-COMPUTE-TUNNEL -s 172.28.200.4/32 -d 172.28.200.1/32 -p tcp --dport 18201 -m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT
        iptables -w -A HC-COMPUTE-TUNNEL -s 172.28.200.5/32 -d 172.28.200.1/32 -p tcp --dport 18301 -m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT
        iptables -w -A HC-COMPUTE-TUNNEL -j REJECT
        iptables -w -I INPUT 1 -d 172.28.200.1/32 -p tcp -m multiport --dports 18005,18006,18201,18301 -j HC-COMPUTE-TUNNEL

        iptables -w -C HC-COMPUTE-TUNNEL -s 172.28.200.3/32 -d 172.28.200.1/32 -p tcp --dport 18005 -m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT
        iptables -w -C HC-COMPUTE-TUNNEL -s 172.28.200.3/32 -d 172.28.200.1/32 -p tcp --dport 18006 -m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT
        iptables -w -C HC-COMPUTE-TUNNEL -s 172.28.200.4/32 -d 172.28.200.1/32 -p tcp --dport 18201 -m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT
        iptables -w -C HC-COMPUTE-TUNNEL -s 172.28.200.5/32 -d 172.28.200.1/32 -p tcp --dport 18301 -m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT
        iptables -w -C HC-COMPUTE-TUNNEL -j REJECT
        while iptables -w -C INPUT -d 172.28.200.1/32 -p tcp -m multiport --dports 18005,18006,18201,18301 -j REJECT 2>/dev/null; do
          iptables -w -D INPUT -d 172.28.200.1/32 -p tcp -m multiport --dports 18005,18006,18201,18301 -j REJECT
        done
      '';
      preStop = ''
        iptables -w -I INPUT 1 -d 172.28.200.1/32 -p tcp -m multiport --dports 18005,18006,18201,18301 -j REJECT
        while iptables -w -C INPUT -d 172.28.200.1/32 -p tcp -m multiport --dports 18005,18006,18201,18301 -j HC-COMPUTE-TUNNEL 2>/dev/null; do
          iptables -w -D INPUT -d 172.28.200.1/32 -p tcp -m multiport --dports 18005,18006,18201,18301 -j HC-COMPUTE-TUNNEL
        done
        if iptables -w -L HC-COMPUTE-TUNNEL -n >/dev/null 2>&1; then
          iptables -w -F HC-COMPUTE-TUNNEL
          iptables -w -X HC-COMPUTE-TUNNEL
        fi
      '';
    };

    systemd.services.homecompute-compute-ssh-tunnel = {
      description = "Opt-in SSH forwards from the control-plane bridge to home-spark loopback";
      wantedBy = [ "multi-user.target" ];
      wants = [ "network-online.target" "docker.service" ];
      after = [
        "network-online.target"
        "docker.service"
        "homecompute-compute-ssh-tunnel-firewall.service"
      ];
      requires = [ "homecompute-compute-ssh-tunnel-firewall.service" ];
      unitConfig = {
        StartLimitIntervalSec = 0;
      };
      serviceConfig = {
        Type = "simple";
        ExecStart = "${tunnelRunner}/bin/homecompute-compute-ssh-tunnel";
        Restart = "always";
        RestartSec = "5s";
        TimeoutStartSec = "75s";
        KillMode = "mixed";
        UMask = "0077";

        AmbientCapabilities = "";
        CapabilityBoundingSet = "";
        DevicePolicy = "closed";
        LockPersonality = true;
        MemoryDenyWriteExecute = true;
        NoNewPrivileges = true;
        PrivateDevices = true;
        PrivateTmp = true;
        ProtectClock = true;
        ProtectControlGroups = true;
        ProtectHome = true;
        ProtectHostname = true;
        ProtectKernelLogs = true;
        ProtectKernelModules = true;
        ProtectKernelTunables = true;
        ProtectProc = "invisible";
        ProtectSystem = "strict";
        ReadOnlyPaths = [ cfg.identityFile cfg.knownHostsFile ];
        # The preflight uses `ip address`, which needs a read-only netlink
        # socket before ssh itself opens the IPv4 forwarding connection.
        RestrictAddressFamilies = [ "AF_INET" "AF_NETLINK" "AF_UNIX" ];
        RestrictNamespaces = true;
        RestrictRealtime = true;
        RestrictSUIDSGID = true;
        SystemCallArchitectures = "native";
        SystemCallFilter = [ "@system-service" "~@privileged" ];
      };
    };
  };
}
