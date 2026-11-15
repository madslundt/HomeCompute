{ config, lib, pkgs, ... }:
let
  bridge = "br-hc-nod";
  tap = "tap-hc-nod";
  host = "10.77.60.1";
  guest = "10.77.60.2";
  interface = "enp44s0";
  mac = "52:54:00:77:60:02";
  state = "/srv/state/nod-vm";
  account = "homecompute-nod-vm";
  unit = "homecompute-nod-vm.service";
  memoryMiB = 8192;
  image = pkgs.fetchurl {
    inherit (config.homecompute.toolboxVm.image) url hash;
  };
  userData = pkgs.writeText "nod-user-data" ''
    #cloud-config
    hostname: homecompute-nod
    disable_root: true
    ssh_pwauth: false
    users:
      - name: nod-operator
        groups: [sudo]
        sudo: ALL=(ALL) NOPASSWD:ALL
        shell: /bin/bash
        lock_passwd: true
        ssh_authorized_keys:
    ${lib.concatMapStringsSep "\n" (key: "      - " + key) config.homecompute.agentsVm.sshAuthorizedKeys}
  '';
  networkData = pkgs.writeText "nod-network-config" ''
    version: 2
    ethernets:
      nod:
        match: {macaddress: "${mac}"}
        addresses: [${guest}/30]
        routes: [{to: 0.0.0.0/0, via: ${host}}]
        nameservers: {addresses: [${host}]}
        dhcp6: false
        accept-ra: false
  '';
  seed = pkgs.runCommand "homecompute-nod-seed.iso" { nativeBuildInputs = [ pkgs.xorriso ]; } ''
    mkdir seed
    cp ${userData} seed/user-data
    cp ${networkData} seed/network-config
    printf 'instance-id: homecompute-nod-v1\nlocal-hostname: homecompute-nod\n' > seed/meta-data
    xorriso -as mkisofs -quiet -volid cidata -joliet -rock -output "$out" seed/*
  '';
  caddyConfig = pkgs.writeText "nod-gateway.Caddyfile" ''
    {
      admin off
      auto_https disable_redirects
      skip_install_trust
    }

    (litellm) {
      reverse_proxy https://192.168.30.122 {
        header_up Host ai.home.arpa
        transport http {
          tls_server_name ai.home.arpa
          tls_trust_pool file /run/credentials/homecompute-nod-model-edge.service/upstream-ca
          dial_timeout 4s
          response_header_timeout 10m
        }
      }
    }

    https://ai.home.arpa:8444 {
      bind ${host}
      tls internal
      @models {
        method GET
        path /v1/models
      }
      @chat {
        method POST
        path /v1/chat/completions
      }
      @authorized header Authorization "Bearer *"
      route @authorized {
        handle @models {
          import litellm
        }
        handle @chat {
          import litellm
        }
        respond 404
      }
      respond 401
    }
  '';
  corefile = pkgs.writeText "nod-Corefile" ''
    .:53 {
      bind ${host}
      hosts {
        ${host} ai.home.arpa
        fallthrough
      }
      forward . /etc/resolv.conf
      cache 60
    }
  '';
in {
  options.homecompute.nodVm.enable = lib.mkEnableOption "isolated Nod server pilot guest";
  config = lib.mkIf config.homecompute.nodVm.enable {
    assertions = [{
      assertion = config.homecompute.agentsVm.sshAuthorizedKeys != [];
      message = "The Nod guest requires a HomeCompute operator SSH key.";
    }];

    users.groups.${account} = {};
    users.users.${account} = {
      isSystemUser = true;
      group = account;
      extraGroups = [ "kvm" ];
    };
    systemd.tmpfiles.rules = [
      "d ${state} 0750 ${account} ${account} - -"
      "a+ /srv/state - - - - u:${account}:--x"
    ];
    networking.networkmanager.unmanaged = [ "interface-name:${bridge}" "interface-name:${tap}" ];
    networking.firewall.interfaces.${bridge} = {
      allowedUDPPorts = [ 53 ];
      allowedTCPPorts = [ 53 8444 ];
    };

    systemd.services.homecompute-nod-network = {
      description = "Host-only network for the Nod guest";
      before = [ unit ];
      partOf = [ unit ];
      path = [ pkgs.iproute2 ];
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
      script = ''
        ip link show ${bridge} >/dev/null 2>&1 || ip link add ${bridge} type bridge
        ip addr replace ${host}/30 dev ${bridge}
        ip link set ${bridge} up
        ip link show ${tap} >/dev/null 2>&1 || ip tuntap add ${tap} mode tap user ${account}
        ip link set ${tap} master ${bridge}
        ip link set ${tap} up
      '';
      postStop = ''
        if ip link show ${tap} >/dev/null 2>&1; then ip link delete ${tap}; fi
        if ip link show ${bridge} >/dev/null 2>&1; then ip link delete ${bridge}; fi
      '';
    };

    systemd.services.homecompute-nod-egress = {
      description = "Restrict Nod guest forwarding to public HTTP and HTTPS";
      requires = [ "homecompute-nod-network.service" ];
      after = [ "homecompute-nod-network.service" "firewall.service" "docker.service" ];
      partOf = [ unit "firewall.service" ];
      path = [ pkgs.iptables ];
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
      script = ''
        iptables -w -I FORWARD 1 -i ${bridge} -j REJECT
        iptables -w -I FORWARD 1 -o ${bridge} -j REJECT
        iptables -w -N HC-NOD-EGRESS
        for blocked in 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.0.0.0/24 192.168.0.0/16 198.18.0.0/15 224.0.0.0/4 240.0.0.0/4; do
          iptables -w -A HC-NOD-EGRESS -d "$blocked" -j REJECT
        done
        # Nod's workspace uses its two pinned public resolvers.
        for resolver in 1.1.1.1 8.8.8.8; do
          iptables -w -A HC-NOD-EGRESS -s ${guest}/32 -o ${interface} -d "$resolver" -p udp --dport 53 -j ACCEPT
          iptables -w -A HC-NOD-EGRESS -s ${guest}/32 -o ${interface} -d "$resolver" -p tcp --dport 53 -j ACCEPT
        done
        iptables -w -A HC-NOD-EGRESS -s ${guest}/32 -o ${interface} -p tcp -m multiport --dports 80,443 -j ACCEPT
        iptables -w -A HC-NOD-EGRESS -j REJECT
        iptables -w -I FORWARD 1 -i ${bridge} -j HC-NOD-EGRESS
        iptables -w -I FORWARD 1 -i ${interface} -o ${bridge} -d ${guest}/32 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
        iptables -w -t nat -A POSTROUTING -s ${guest}/32 -o ${interface} -j MASQUERADE
        ip6tables -w -I FORWARD 1 -i ${bridge} -j REJECT
        ip6tables -w -I FORWARD 1 -o ${bridge} -j REJECT
      '';
      postStop = ''
        iptables -w -D FORWARD -i ${bridge} -j HC-NOD-EGRESS || true
        iptables -w -D FORWARD -i ${interface} -o ${bridge} -d ${guest}/32 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT || true
        iptables -w -t nat -D POSTROUTING -s ${guest}/32 -o ${interface} -j MASQUERADE || true
        iptables -w -F HC-NOD-EGRESS || true
        iptables -w -X HC-NOD-EGRESS || true
        iptables -w -D FORWARD -i ${bridge} -j REJECT || true
        iptables -w -D FORWARD -o ${bridge} -j REJECT || true
        ip6tables -w -D FORWARD -i ${bridge} -j REJECT || true
        ip6tables -w -D FORWARD -o ${bridge} -j REJECT || true
      '';
    };

    systemd.services.homecompute-nod-dns = {
      description = "DNS for the Nod guest";
      requires = [ "homecompute-nod-network.service" ];
      after = [ "homecompute-nod-network.service" ];
      partOf = [ unit ];
      serviceConfig = {
        DynamicUser = true;
        AmbientCapabilities = [ "CAP_NET_BIND_SERVICE" ];
        CapabilityBoundingSet = [ "CAP_NET_BIND_SERVICE" ];
        ExecStart = "${pkgs.coredns}/bin/coredns -conf ${corefile}";
        NoNewPrivileges = true;
      };
    };

    systemd.services.homecompute-nod-model-edge = {
      description = "Chat-only TLS gateway for the Nod guest";
      requires = [ "homecompute-nod-network.service" ];
      after = [ "homecompute-nod-network.service" ];
      partOf = [ unit ];
      environment.XDG_DATA_HOME = "/var/lib/homecompute-nod-edge";
      serviceConfig = {
        # The upstream Caddy data directory is owner-only. Bind just the public
        # certificate into the service's private filesystem namespace.
        DynamicUser = true;
        StateDirectory = "homecompute-nod-edge";
        LoadCredential = "upstream-ca:/srv/state/control-plane/caddy-data/caddy/pki/authorities/local/root.crt";
        InaccessiblePaths = [ "/srv/state" ];
        ExecStart = "${pkgs.caddy}/bin/caddy run --adapter caddyfile --config ${caddyConfig}";
        NoNewPrivileges = true;
      };
    };

    systemd.services.homecompute-nod-vm = {
      description = "Dedicated Linux guest for the Nod server";
      requires = [
        "homecompute-nod-network.service" "homecompute-nod-egress.service"
        "homecompute-nod-dns.service" "homecompute-nod-model-edge.service"
      ];
      after = [
        "homecompute-nod-network.service" "homecompute-nod-egress.service"
        "homecompute-nod-dns.service" "homecompute-nod-model-edge.service"
      ];
      path = [ pkgs.qemu_kvm pkgs.socat pkgs.gawk ];
      preStart = ''
        available_kib="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
        if [ "$available_kib" -lt ${toString ((memoryMiB + 4096) * 1024)} ]; then
          echo 'Nod needs 8 GiB plus 4 GiB host headroom.' >&2
          exit 1
        fi
        if [ ! -e ${state}/root.qcow2 ]; then
          umask 077
          trap 'rm -f ${state}/root.qcow2.new' EXIT
          qemu-img convert -f qcow2 -O qcow2 ${image} ${state}/root.qcow2.new
          qemu-img resize ${state}/root.qcow2.new 60G
          mv ${state}/root.qcow2.new ${state}/root.qcow2
        fi
        rm -f ${state}/qmp.sock
      '';
      script = ''
        exec qemu-system-x86_64 -name homecompute-nod -machine q35,accel=kvm -cpu host \
          -m ${toString memoryMiB} -smp 2 -nodefaults -display none -serial stdio -no-reboot \
          -sandbox on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny \
          -device virtio-rng-pci -qmp unix:${state}/qmp.sock,server=on,wait=off \
          -drive file=${state}/root.qcow2,if=virtio,format=qcow2,cache=none,discard=unmap \
          -drive file=${seed},if=virtio,format=raw,readonly=on \
          -netdev tap,id=nod,ifname=${tap},script=no,downscript=no \
          -device virtio-net-pci,netdev=nod,mac=${mac}
      '';
      preStop = ''
        if [ -S ${state}/qmp.sock ]; then
          printf '%s\n%s\n' '{"execute":"qmp_capabilities"}' '{"execute":"system_powerdown"}' \
            | socat - UNIX-CONNECT:${state}/qmp.sock || true
          while kill -0 "$MAINPID" 2>/dev/null; do sleep 1; done
        fi
      '';
      serviceConfig = {
        User = account;
        Group = account;
        PrivateTmp = true;
        ProtectHome = true;
        ProtectSystem = "strict";
        ReadWritePaths = [ state ];
        DeviceAllow = [ "/dev/kvm rw" "/dev/net/tun rw" ];
        NoNewPrivileges = true;
        TimeoutStopSec = "2min";
      };
    };
  };
}
