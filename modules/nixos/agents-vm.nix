{
  config,
  lib,
  pkgs,
  ...
}:
let
  cfg = config.homecompute.agentsVm;
  bridge = cfg.network.bridgeName;
  tap = cfg.network.tapName;
  guestAddress = cfg.network.guestAddress;
  hostAddress = cfg.network.hostAddress;
  prefixLength = toString cfg.network.prefixLength;
  inferenceBridgePort = 18080;
  statePath = "/srv/state/agents-vm";
  guestDisk = "${statePath}/agents.qcow2";

  image = pkgs.fetchurl {
    inherit (cfg.image) url hash;
  };

  sshKeys = lib.concatMapStringsSep "\n" (key: "      - ${key}") cfg.sshAuthorizedKeys;
  allowedTcpRules = lib.concatMapStringsSep "\n" (port: ''
    iptables -w -A HC-AGENTS-EGRESS -s ${guestAddress}/32 -p tcp --dport ${toString port} -j ACCEPT
  '') cfg.network.allowedInternetTcpPorts;
  maintenanceRules = lib.optionalString cfg.network.maintenanceEgress ''
    iptables -w -A HC-AGENTS-EGRESS -s ${guestAddress}/32 -p tcp -m multiport --dports 80,443 -j ACCEPT
    iptables -w -A HC-AGENTS-EGRESS -s ${guestAddress}/32 -p udp --dport 123 -j ACCEPT
  '';

  userData = pkgs.writeText "agents-vm-user-data" ''
    #cloud-config
    hostname: agents
    manage_etc_hosts: true
    disable_root: true
    ssh_pwauth: false
    users:
      - name: ${cfg.operatorUser}
        gecos: HomeCompute agents operator
        groups: [adm, sudo]
        sudo: ALL=(ALL) NOPASSWD:ALL
        shell: /bin/bash
        lock_passwd: true
        ssh_authorized_keys:
    ${sshKeys}
    write_files:
      - path: /etc/ssh/sshd_config.d/90-homecompute.conf
        owner: root:root
        permissions: '0644'
        content: |
          PasswordAuthentication no
          PermitRootLogin no
          AllowUsers ${cfg.operatorUser}
    runcmd:
      - [systemctl, restart, ssh.service]
  '';

  metaData = pkgs.writeText "agents-vm-meta-data" ''
    instance-id: homecompute-agents-v1
    local-hostname: agents
  '';

  networkData = pkgs.writeText "agents-vm-network-config" ''
    version: 2
    ethernets:
      ens3:
        match:
          macaddress: "52:54:00:77:20:02"
        set-name: ens3
        addresses:
          - ${guestAddress}/${prefixLength}
        routes:
          - to: 0.0.0.0/0
            via: ${hostAddress}
        nameservers:
          addresses:
            - ${hostAddress}
          search:
            - home.arpa
  '';

  seedImage = pkgs.runCommand "homecompute-agents-seed.iso" { nativeBuildInputs = [ pkgs.xorriso ]; } ''
    mkdir -p seed
    cp ${userData} seed/user-data
    cp ${metaData} seed/meta-data
    cp ${networkData} seed/network-config
    xorriso -as mkisofs -quiet -volid cidata -joliet -rock \
      -output "$out" seed/user-data seed/meta-data seed/network-config
  '';
in
{
  options.homecompute.agentsVm = {
    enable = lib.mkEnableOption "isolated Ubuntu KVM guest for household agents";

    image = {
      url = lib.mkOption {
        type = lib.types.str;
        default = "https://cloud-images.ubuntu.com/releases/noble/release-20260911/ubuntu-24.04-server-cloudimg-amd64.img";
        description = "Immutable Ubuntu 24.04 cloud-image URL.";
      };
      hash = lib.mkOption {
        type = lib.types.str;
        default = "sha256-YSssDMG8QTpsuMOP1hF5TK8PK0NsUAE9izeU2xKtc1Q=";
        description = "SRI SHA-256 of the exact Ubuntu cloud image.";
      };
    };

    sshAuthorizedKeys = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = "Public SSH keys installed for the guest operator account.";
    };

    operatorUser = lib.mkOption {
      type = lib.types.strMatching "[a-z_][a-z0-9_-]*";
      default = "hermes-operator";
      description = "Unprivileged administrative user created by cloud-init.";
    };

    dataClassification = lib.mkOption {
      type = lib.types.enum [
        "synthetic-only"
        "household"
      ];
      default = "household";
      description = "Maximum data class permitted in the guest.";
    };

    resources = {
      vcpus = lib.mkOption {
        type = lib.types.ints.positive;
        default = 4;
      };
      memoryMiB = lib.mkOption {
        type = lib.types.ints.positive;
        default = 16384;
      };
      diskGiB = lib.mkOption {
        type = lib.types.ints.positive;
        default = 80;
      };
    };

    network = {
      bridgeName = lib.mkOption {
        type = lib.types.str;
        default = "br-hc-agents";
      };
      tapName = lib.mkOption {
        type = lib.types.str;
        default = "tap-hc-agents";
      };
      hostAddress = lib.mkOption {
        type = lib.types.str;
        default = "10.77.20.1";
      };
      guestAddress = lib.mkOption {
        type = lib.types.str;
        default = "10.77.20.2";
      };
      prefixLength = lib.mkOption {
        type = lib.types.ints.between 1 32;
        default = 30;
      };
      upstreamInterface = lib.mkOption {
        type = lib.types.str;
        default = "enp44s0";
        description = "Interface used only for explicitly permitted public egress.";
      };
      allowedInternetTcpPorts = lib.mkOption {
        type = lib.types.listOf lib.types.port;
        default = [ ];
        description = "Public Internet TCP ports available outside maintenance windows.";
      };
      maintenanceEgress = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = "Temporarily permit public HTTP(S) and NTP for reviewed guest maintenance.";
      };
    };
  };

  config = lib.mkMerge [
    {
      # Keep the state path present while the guest is staged but disabled so
      # the local bootstrap job has a deterministic, non-missing source.
      users.groups.homecompute-agents-vm = { };
      users.users.homecompute-agents-vm = {
        isSystemUser = true;
        group = "homecompute-agents-vm";
        extraGroups = [ "kvm" ];
      };
      systemd.tmpfiles.rules = [
        "d ${statePath} 0750 homecompute-agents-vm homecompute-agents-vm - -"
        # /srv/state is intentionally not searchable by unrelated services.
        # Grant this VM account traversal only; the child remains its private
        # 0750 directory and no other state directory becomes readable.
        "a+ /srv/state - - - - u:homecompute-agents-vm:--x"
      ];
    }

    (lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.sshAuthorizedKeys != [ ];
        message = "homecompute.agentsVm.sshAuthorizedKeys must contain at least one reviewed public key";
      }
      {
        assertion =
          config.homecompute.backups.enable
          || (
            cfg.dataClassification == "synthetic-only"
            && config.homecompute.agentsVm.localBootstrapBackup.enable
            && config.homecompute.agentsVm.localBootstrapBackup.riskAccepted
          );
        message = "household data requires off-host backup; local bootstrap permits synthetic-only pilots";
      }
      {
        assertion = lib.any (
          path: path == "/srv/state" || path == statePath
        ) config.homecompute.backups.paths;
        message = "homecompute.backups.paths must include /srv/state or the agents VM state directory";
      }
      {
        assertion = lib.hasPrefix "https://cloud-images.ubuntu.com/" cfg.image.url;
        message = "homecompute.agentsVm.image.url must use the official Ubuntu HTTPS origin";
      }
      {
        assertion = builtins.match "sha256-[A-Za-z0-9+/]{43}=" cfg.image.hash != null;
        message = "homecompute.agentsVm.image.hash must be a pinned SRI SHA-256";
      }
      {
        assertion = cfg.resources.memoryMiB >= 8192 && cfg.resources.diskGiB >= 40;
        message = "homecompute.agentsVm resources must meet the NemoClaw minimums (8 GiB RAM and 40 GiB disk)";
      }
    ];

    networking.networkmanager.unmanaged = [
      "interface-name:${bridge}"
      "interface-name:${tap}"
    ];
    networking.firewall.interfaces.${bridge} = {
      allowedTCPPorts = [
        53
        443
        inferenceBridgePort
      ];
      allowedUDPPorts = [ 53 ];
    };
    boot.kernel.sysctl = {
      "net.ipv4.ip_forward" = 1;
      "net.ipv4.conf.${bridge}.forwarding" = 1;
      "net.ipv4.conf.${bridge}.accept_redirects" = 0;
      "net.ipv4.conf.${bridge}.send_redirects" = 0;
    };

    systemd.services.homecompute-agents-network = {
      description = "HomeCompute agents host-only bridge";
      wantedBy = [ "multi-user.target" ];
      before = [
        "homecompute-agents-firewall.service"
        "homecompute-agents-vm.service"
        "homecompute-agents-dns.service"
        "homecompute-agents-ai-proxy.socket"
      ];
      path = [ pkgs.iproute2 ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
      };
      script = ''
        ip link show ${bridge} >/dev/null 2>&1 || ip link add ${bridge} type bridge
        ip addr replace ${hostAddress}/${prefixLength} dev ${bridge}
        ip link set ${bridge} up
        if ! ip link show ${tap} >/dev/null 2>&1; then
          ip tuntap add dev ${tap} mode tap user homecompute-agents-vm group homecompute-agents-vm
        fi
        ip link set ${tap} master ${bridge}
        ip link set ${tap} up
      '';
      preStop = ''
        ip link set ${tap} down 2>/dev/null || true
        ip link delete ${tap} 2>/dev/null || true
        ip link set ${bridge} down 2>/dev/null || true
        ip link delete ${bridge} type bridge 2>/dev/null || true
      '';
    };

    systemd.services.homecompute-agents-firewall = {
      description = "HomeCompute agents default-deny forwarding policy";
      wantedBy = [ "multi-user.target" ];
      requires = [ "homecompute-agents-network.service" ];
      after = [
        "firewall.service"
        "homecompute-agents-network.service"
      ];
      partOf = [ "firewall.service" ];
      before = [ "homecompute-agents-vm.service" ];
      path = [ pkgs.iptables ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
      };
      script = ''
        # A fresh guard keeps a reload fail-closed while the referenced policy
        # chain is flushed and repopulated.
        iptables -w -I FORWARD 1 -i ${bridge} -j REJECT
        iptables -w -N HC-AGENTS-EGRESS 2>/dev/null || true
        iptables -w -F HC-AGENTS-EGRESS
        iptables -w -A HC-AGENTS-EGRESS -s ${guestAddress}/32 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
        iptables -w -A HC-AGENTS-EGRESS ! -s ${guestAddress}/32 -j REJECT
        iptables -w -A HC-AGENTS-EGRESS -d ${hostAddress}/32 -j REJECT
        iptables -w -A HC-AGENTS-EGRESS -d 10.77.10.0/24 -j REJECT
        for subnet in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16 127.0.0.0/8 224.0.0.0/4; do
          iptables -w -A HC-AGENTS-EGRESS -d "$subnet" -j REJECT
        done
        ${allowedTcpRules}
        ${maintenanceRules}
        iptables -w -A HC-AGENTS-EGRESS -j REJECT

        iptables -w -C FORWARD -i ${bridge} -j HC-AGENTS-EGRESS 2>/dev/null || \
          iptables -w -I FORWARD 1 -i ${bridge} -j HC-AGENTS-EGRESS
        iptables -w -C FORWARD -o ${bridge} -d ${guestAddress}/32 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || \
          iptables -w -I FORWARD 1 -o ${bridge} -d ${guestAddress}/32 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
        iptables -w -C FORWARD -o ${bridge} -j REJECT 2>/dev/null || \
          iptables -w -I FORWARD 2 -o ${bridge} -j REJECT
        iptables -w -t nat -C POSTROUTING -s ${guestAddress}/32 -o ${cfg.network.upstreamInterface} -j MASQUERADE 2>/dev/null || \
          iptables -w -t nat -A POSTROUTING -s ${guestAddress}/32 -o ${cfg.network.upstreamInterface} -j MASQUERADE

        ip6tables -w -C FORWARD -i ${bridge} -j REJECT 2>/dev/null || ip6tables -w -I FORWARD 1 -i ${bridge} -j REJECT
        ip6tables -w -C FORWARD -o ${bridge} -j REJECT 2>/dev/null || ip6tables -w -I FORWARD 1 -o ${bridge} -j REJECT
        while iptables -w -C FORWARD -i ${bridge} -j REJECT 2>/dev/null; do
          iptables -w -D FORWARD -i ${bridge} -j REJECT
        done
      '';
      preStop = ''
        iptables -w -I FORWARD 1 -i ${bridge} -j REJECT
        while iptables -w -C FORWARD -i ${bridge} -j HC-AGENTS-EGRESS 2>/dev/null; do
          iptables -w -D FORWARD -i ${bridge} -j HC-AGENTS-EGRESS
        done
        if iptables -w -L HC-AGENTS-EGRESS -n >/dev/null 2>&1; then
          iptables -w -F HC-AGENTS-EGRESS
          iptables -w -X HC-AGENTS-EGRESS
        fi
      '';
    };

    systemd.services.homecompute-agents-dns = {
      description = "DNS for the HomeCompute agents guest";
      wantedBy = [ "multi-user.target" ];
      requires = [ "homecompute-agents-network.service" ];
      after = [ "homecompute-agents-network.service" ];
      serviceConfig = {
        DynamicUser = true;
        AmbientCapabilities = [ "CAP_NET_BIND_SERVICE" ];
        CapabilityBoundingSet = [ "CAP_NET_BIND_SERVICE" ];
        ExecStart = "${pkgs.coredns}/bin/coredns -conf ${pkgs.writeText "agents-Corefile" ''
          .:53 {
            bind ${hostAddress}
            hosts {
              ${hostAddress} ai.home.arpa
              fallthrough
            }
            forward . /etc/resolv.conf
            cache 60
          }
        ''}";
        NoNewPrivileges = true;
        PrivateTmp = true;
        ProtectHome = true;
        ProtectSystem = "strict";
        Restart = "on-failure";
      };
    };

    systemd.sockets.homecompute-agents-ai-proxy = {
      description = "Host-only AI TLS edge for the agents guest";
      # This listener binds an address created by the custom bridge service.
      # Starting it from sockets.target creates a boot ordering cycle because
      # that target is reached before the bridge's multi-user service.
      wantedBy = [ "multi-user.target" ];
      requires = [ "homecompute-agents-network.service" ];
      after = [ "homecompute-agents-network.service" ];
      socketConfig = {
        # Give the listening socket to one long-running socket-proxyd process.
        # Per-connection activation would require an inetd-style template and
        # proved less reliable for this opaque TLS stream.
        Accept = false;
        ListenStream = "${hostAddress}:443";
      };
    };
    systemd.services.homecompute-agents-ai-proxy = {
      description = "Proxy an agents guest connection to the local Caddy edge";
      serviceConfig = {
        DynamicUser = true;
        ExecStart = "${pkgs.systemd}/lib/systemd/systemd-socket-proxyd 192.168.30.122:443";
        PrivateTmp = true;
        ProtectHome = true;
        ProtectSystem = "strict";
      };
    };

    # OpenShell 0.0.116's inference router does not load NemoClaw's imported
    # private CA for its reqwest client, even though the CA is installed in the
    # sandbox OS trust store. Keep TLS on every other path and terminate this
    # one compatibility hop only on the isolated /30 bridge. The upstream leg
    # is verified against Caddy's private root and retains the original Host
    # header; LiteLLM still requires the sandbox-specific API key.
    systemd.services.homecompute-agents-ai-http-bridge = {
      description = "Host-only HTTP compatibility bridge for OpenShell inference";
      wantedBy = [ "multi-user.target" ];
      requires = [ "homecompute-agents-network.service" ];
      after = [ "homecompute-agents-network.service" ];
      path = [ pkgs.socat ];
      serviceConfig = {
        ExecStart = "${pkgs.socat}/bin/socat TCP4-LISTEN:${toString inferenceBridgePort},bind=${hostAddress},reuseaddr,fork OPENSSL:192.168.30.122:443,verify=1,cafile=/srv/state/control-plane/caddy-data/caddy/pki/authorities/local/root.crt,commonname=ai.home.arpa,snihost=ai.home.arpa";
        NoNewPrivileges = true;
        PrivateTmp = true;
        ProtectHome = true;
        ProtectSystem = "strict";
        Restart = "on-failure";
        RestartSec = "5s";
      };
    };

    systemd.services.homecompute-agents-memory-preflight = {
      description = "Refuse agents VM startup while the large automation standby is running";
      before = [ "homecompute-agents-vm.service" ];
      path = [
        pkgs.docker
        pkgs.gnugrep
      ];
      serviceConfig.Type = "oneshot";
      script = ''
        if docker inspect --format '{{.State.Running}}' \
          homecompute-control-plane-automation-backup-1 2>/dev/null | grep -qx true; then
          echo "automation-backup is running; refusing to start the 16 GiB agents VM" >&2
          exit 1
        fi
      '';
    };

    systemd.services.homecompute-agents-vm = {
      description = "Isolated Ubuntu guest for household agents";
      wantedBy = [ "multi-user.target" ];
      requires = [
        "homecompute-agents-network.service"
        "homecompute-agents-firewall.service"
        "homecompute-agents-memory-preflight.service"
      ];
      after = [
        "homecompute-agents-network.service"
        "homecompute-agents-firewall.service"
        "homecompute-agents-memory-preflight.service"
      ];
      path = [ pkgs.qemu_kvm ];
      preStart = ''
        test -c /dev/kvm
        if [ -e /run/homecompute/agents-vm-backup-in-progress ]; then
          echo "agents VM backup is in progress; refusing VM start" >&2
          exit 1
        fi
        rm -f ${statePath}/qmp.sock
        if [ ! -e ${guestDisk} ]; then
          umask 0077
          trap 'rm -f ${guestDisk}.new' EXIT
          qemu-img convert -f qcow2 -O qcow2 ${image} ${guestDisk}.new
          qemu-img resize ${guestDisk}.new ${toString cfg.resources.diskGiB}G
          mv ${guestDisk}.new ${guestDisk}
          trap - EXIT
        fi
        test "$(qemu-img info --output=json ${guestDisk} | ${pkgs.jq}/bin/jq -r .format)" = qcow2
      '';
      script = ''
        exec qemu-system-x86_64 \
          -name homecompute-agents \
          -machine q35,accel=kvm \
          -cpu host \
          -smp ${toString cfg.resources.vcpus} \
          -m ${toString cfg.resources.memoryMiB} \
          -nodefaults \
          -display none \
          -serial stdio \
          -no-reboot \
          -sandbox on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny \
          -qmp unix:${statePath}/qmp.sock,server=on,wait=off \
          -device virtio-rng-pci \
          -drive file=${guestDisk},if=virtio,format=qcow2,cache=none,discard=unmap \
          -drive file=${seedImage},if=virtio,format=raw,readonly=on \
          -netdev tap,id=agentsnet,ifname=${tap},script=no,downscript=no \
          -device virtio-net-pci,netdev=agentsnet,mac=52:54:00:77:20:02
      '';
      preStop = ''
        if [ -S ${statePath}/qmp.sock ]; then
          printf '%s\n%s\n' \
            '{"execute":"qmp_capabilities"}' \
            '{"execute":"system_powerdown"}' | \
            ${pkgs.socat}/bin/socat - UNIX-CONNECT:${statePath}/qmp.sock || true
        fi
        # ExecStop must remain active until QEMU exits; otherwise systemd sends
        # the fallback signal immediately after this script returns.
        for attempt in $(${pkgs.coreutils}/bin/seq 1 110); do
          if ! kill -0 "$MAINPID" 2>/dev/null; then
            exit 0
          fi
          ${pkgs.coreutils}/bin/sleep 1
        done
        exit 1
      '';
      serviceConfig = {
        User = "homecompute-agents-vm";
        Group = "homecompute-agents-vm";
        SupplementaryGroups = [ "kvm" ];
        Restart = "on-failure";
        RestartSec = "10s";
        TimeoutStopSec = "2m";
        # Only used after the bounded ACPI/QMP shutdown wait above expires.
        KillSignal = "SIGTERM";
        NoNewPrivileges = true;
        PrivateTmp = true;
        ProtectHome = true;
        ProtectSystem = "strict";
        ReadWritePaths = [ statePath ];
        DeviceAllow = [
          "/dev/kvm rw"
          "/dev/net/tun rw"
        ];
      };
    };
    })
  ];
}
