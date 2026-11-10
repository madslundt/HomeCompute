{ config, lib, pkgs, ... }:
let
  cfg = config.homecompute.toolboxVm;
  bridge = cfg.network.bridge;
  tap = cfg.network.tap;
  host = cfg.network.host;
  guest = cfg.network.guest;
  disk = "/srv/state/toolbox-vm/root.qcow2";
  image = pkgs.fetchurl { inherit (cfg.image) url hash; };
  userdata = pkgs.writeText "toolbox-userdata" ''
    #cloud-config
    hostname: craft-toolbox
    disable_root: true
    ssh_pwauth: false
    users:
      - name: ${cfg.user}
        groups: [adm, sudo]
        sudo: ALL=(ALL) NOPASSWD:ALL
        shell: /bin/bash
        lock_passwd: true
        ssh_authorized_keys:
    ${lib.concatMapStringsSep "\n" (key: "      - " + key) cfg.sshKeys}
  '';
  metadata = pkgs.writeText "toolbox-meta" "instance-id: toolbox-v1\nlocal-hostname: craft-toolbox\n";
  networkData = pkgs.writeText "toolbox-network" ''
    version: 2
    ethernets:
      ens3:
        addresses: [${guest}/30]
        routes: [{to: 0.0.0.0/0, via: ${host}}]
        nameservers: {addresses: [${host}]}
  '';
  seed = pkgs.runCommand "homecompute-toolbox-seed.iso" { nativeBuildInputs = [ pkgs.xorriso ]; } ''
    mkdir seed
    cp ${userdata} seed/user-data
    cp ${metadata} seed/meta-data
    cp ${networkData} seed/network-config
    xorriso -as mkisofs -quiet -volid cidata -joliet -rock -output "$out" seed/*
  '';
in {
  options.homecompute.toolboxVm = {
    enable = lib.mkEnableOption "isolated Craft Anything KVM guest";
    user = lib.mkOption { type = lib.types.strMatching "[a-z_][a-z0-9_-]*"; default = "toolbox-operator"; };
    sshKeys = lib.mkOption { type = lib.types.listOf lib.types.str; default = []; };
    image.url = lib.mkOption { type = lib.types.str; default = "https://cloud-images.ubuntu.com/releases/noble/release-20260911/ubuntu-24.04-server-cloudimg-amd64.img"; };
    image.hash = lib.mkOption { type = lib.types.str; default = "sha256-YSssDMG8QTpsuMOP1hF5TK8PK0NsUAE9izeU2xKtc1Q="; };
    resources = {
      vcpus = lib.mkOption { type = lib.types.ints.positive; default = 4; };
      memoryMiB = lib.mkOption { type = lib.types.ints.positive; default = 8192; };
      diskGiB = lib.mkOption { type = lib.types.ints.positive; default = 100; };
    };
    network = {
      bridge = lib.mkOption { type = lib.types.str; default = "br-hc-toolbox"; };
      tap = lib.mkOption { type = lib.types.str; default = "tap-hc-toolbox"; };
      host = lib.mkOption { type = lib.types.str; default = "10.77.30.1"; };
      guest = lib.mkOption { type = lib.types.str; default = "10.77.30.2"; };
      upstream = lib.mkOption { type = lib.types.str; default = "enp44s0"; };
    };
  };
  config = lib.mkIf cfg.enable {
    assertions = [
      { assertion = config.homecompute.nasRecoveryBackup.enable && config.homecompute.nasRecoveryBackup.includeApplicationState; message = "toolbox VM requires off-host NAS application-state recovery coverage"; }
      { assertion = cfg.sshKeys != []; message = "toolbox VM requires an operator SSH key"; }
      { assertion = lib.hasPrefix "https://cloud-images.ubuntu.com/" cfg.image.url; message = "toolbox image must use official Ubuntu HTTPS"; }
      { assertion = builtins.match "sha256-[A-Za-z0-9+/]{43}=" cfg.image.hash != null; message = "toolbox image must be SHA-256 pinned"; }
      { assertion = cfg.resources.memoryMiB >= 4096 && cfg.resources.diskGiB >= 40; message = "toolbox needs at least 4 GiB RAM and 40 GiB disk"; }
    ];
    users.groups.homecompute-toolbox-vm = {};
    users.users.homecompute-toolbox-vm = { isSystemUser = true; group = "homecompute-toolbox-vm"; extraGroups = ["kvm"]; };
    systemd.tmpfiles.rules = [
      "d /srv/state/toolbox-vm 0750 homecompute-toolbox-vm homecompute-toolbox-vm - -"
      "a+ /srv/state - - - - u:homecompute-toolbox-vm:--x"
    ];
    networking.networkmanager.unmanaged = ["interface-name:${bridge}" "interface-name:${tap}"];
    networking.firewall.interfaces.${bridge}.allowedUDPPorts = [53];
    systemd.services.homecompute-toolbox-dns = {
      wantedBy = ["multi-user.target"];
      requires = ["homecompute-toolbox-network.service"];
      after = ["homecompute-toolbox-network.service"];
      serviceConfig = {
        DynamicUser = true;
        AmbientCapabilities = ["CAP_NET_BIND_SERVICE"];
        CapabilityBoundingSet = ["CAP_NET_BIND_SERVICE"];
        ExecStart = "${pkgs.coredns}/bin/coredns -conf ${pkgs.writeText "toolbox-Corefile" ''
          .:53 {
            bind ${host}
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
    boot.kernel.sysctl."net.ipv4.ip_forward" = 1;
    systemd.services.homecompute-toolbox-network = {
      wantedBy = ["multi-user.target"]; before = ["homecompute-toolbox-firewall.service" "homecompute-toolbox-vm.service"];
      path = [pkgs.iproute2]; serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
      script = ''
        ip link show ${bridge} >/dev/null 2>&1 || ip link add ${bridge} type bridge
        ip addr replace ${host}/30 dev ${bridge}
        ip link set ${bridge} up
        ip link show ${tap} >/dev/null 2>&1 || ip tuntap add ${tap} mode tap user homecompute-toolbox-vm group homecompute-toolbox-vm
        ip link set ${tap} master ${bridge}
        ip link set ${tap} up
      '';
      preStop = "ip link delete ${tap} 2>/dev/null || true; ip link delete ${bridge} type bridge 2>/dev/null || true";
    };
    systemd.services.homecompute-toolbox-firewall = {
      wantedBy = ["multi-user.target"]; requires = ["homecompute-toolbox-network.service"];
      after = ["firewall.service" "homecompute-toolbox-network.service"]; before = ["homecompute-toolbox-vm.service"];
      partOf = ["firewall.service"]; path = [pkgs.iptables];
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
      script = ''
        iptables -w -I FORWARD 1 -i ${bridge} -j REJECT
        iptables -w -N HC-TOOLBOX-EGRESS 2>/dev/null || true
        iptables -w -F HC-TOOLBOX-EGRESS
        iptables -w -A HC-TOOLBOX-EGRESS -s ${guest}/32 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
        for subnet in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16 127.0.0.0/8 224.0.0.0/4; do iptables -w -A HC-TOOLBOX-EGRESS -d "$subnet" -j REJECT; done
        iptables -w -A HC-TOOLBOX-EGRESS -s ${guest}/32 -p tcp -m multiport --dports 80,443 -j ACCEPT
        iptables -w -A HC-TOOLBOX-EGRESS -j REJECT
        iptables -w -C FORWARD -i ${bridge} -j HC-TOOLBOX-EGRESS 2>/dev/null || iptables -w -I FORWARD 1 -i ${bridge} -j HC-TOOLBOX-EGRESS
        iptables -w -C FORWARD -o ${bridge} -d ${guest}/32 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null || iptables -w -I FORWARD 1 -o ${bridge} -d ${guest}/32 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
        iptables -w -t nat -C POSTROUTING -s ${guest}/32 -o ${cfg.network.upstream} -j MASQUERADE 2>/dev/null || iptables -w -t nat -A POSTROUTING -s ${guest}/32 -o ${cfg.network.upstream} -j MASQUERADE
        ip6tables -w -C FORWARD -i ${bridge} -j REJECT 2>/dev/null || ip6tables -w -I FORWARD 1 -i ${bridge} -j REJECT
        ip6tables -w -C FORWARD -o ${bridge} -j REJECT 2>/dev/null || ip6tables -w -I FORWARD 1 -o ${bridge} -j REJECT
        while iptables -w -C FORWARD -i ${bridge} -j REJECT 2>/dev/null; do iptables -w -D FORWARD -i ${bridge} -j REJECT; done
      '';
      preStop = ''
        iptables -w -I FORWARD 1 -i ${bridge} -j REJECT
        while iptables -w -C FORWARD -i ${bridge} -j HC-TOOLBOX-EGRESS 2>/dev/null; do
          iptables -w -D FORWARD -i ${bridge} -j HC-TOOLBOX-EGRESS
        done
        while iptables -w -C FORWARD -o ${bridge} -d ${guest}/32 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT 2>/dev/null; do
          iptables -w -D FORWARD -o ${bridge} -d ${guest}/32 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
        done
        while iptables -w -C FORWARD -o ${bridge} -j REJECT 2>/dev/null; do
          iptables -w -D FORWARD -o ${bridge} -j REJECT
        done
        iptables -w -t nat -D POSTROUTING -s ${guest}/32 -o ${cfg.network.upstream} -j MASQUERADE 2>/dev/null || true
        ip6tables -w -D FORWARD -i ${bridge} -j REJECT 2>/dev/null || true
        ip6tables -w -D FORWARD -o ${bridge} -j REJECT 2>/dev/null || true
        iptables -w -F HC-TOOLBOX-EGRESS 2>/dev/null || true
        iptables -w -X HC-TOOLBOX-EGRESS 2>/dev/null || true
      '';
    };
    systemd.services.homecompute-toolbox-vm = {
      description = "Isolated Craft Anything toolbox guest"; wantedBy = ["multi-user.target"];
      requires = ["homecompute-toolbox-network.service" "homecompute-toolbox-firewall.service" "homecompute-toolbox-memory-preflight.service"];
      after = ["homecompute-toolbox-network.service" "homecompute-toolbox-firewall.service" "homecompute-toolbox-memory-preflight.service"];
      path = [pkgs.qemu_kvm pkgs.jq pkgs.socat pkgs.coreutils pkgs.gawk];
      preStart = ''
        test -c /dev/kvm
        if [ -e /run/homecompute/toolbox-vm-backup-in-progress ]; then exit 1; fi
        rm -f /srv/state/toolbox-vm/qmp.sock
        if [ ! -e /srv/state/toolbox-vm/root.qcow2 ]; then umask 0077; qemu-img convert -f qcow2 -O qcow2 ${image} /srv/state/toolbox-vm/root.qcow2; qemu-img resize /srv/state/toolbox-vm/root.qcow2 ${toString cfg.resources.diskGiB}G; fi
        test "$(qemu-img info --output=json ${disk} | jq -r .format)" = qcow2
      '';
      script = ''
        exec qemu-system-x86_64 -name homecompute-toolbox -machine q35,accel=kvm -cpu host -smp ${toString cfg.resources.vcpus} -m ${toString cfg.resources.memoryMiB} -nodefaults -display none -serial stdio -no-reboot -sandbox on,obsolete=deny,elevateprivileges=deny,spawn=deny,resourcecontrol=deny -qmp unix:/srv/state/toolbox-vm/qmp.sock,server=on,wait=off -device virtio-rng-pci -drive file=${disk},if=virtio,format=qcow2,cache=none,discard=unmap -drive file=${seed},if=virtio,format=raw,readonly=on -netdev tap,id=toolboxnet,ifname=${tap},script=no,downscript=no -device virtio-net-pci,netdev=toolboxnet,mac=52:54:00:77:30:02
      '';
      preStop = ''
        if [ -S /srv/state/toolbox-vm/qmp.sock ]; then printf '%s\n%s\n' '{"execute":"qmp_capabilities"}' '{"execute":"system_powerdown"}' | socat - UNIX-CONNECT:/srv/state/toolbox-vm/qmp.sock || true; fi
        for attempt in $(seq 1 110); do if ! kill -0 "$MAINPID" 2>/dev/null; then exit 0; fi; sleep 1; done
        exit 1
      '';
      serviceConfig = {
        User = "homecompute-toolbox-vm"; Group = "homecompute-toolbox-vm"; SupplementaryGroups = ["kvm"];
        Restart = "always"; RestartSec = "10s"; TimeoutStopSec = "2m"; NoNewPrivileges = true;
        PrivateTmp = true; ProtectHome = true; ProtectSystem = "strict"; ReadWritePaths = ["/srv/state/toolbox-vm"];
        DeviceAllow = ["/dev/kvm rw" "/dev/net/tun rw"];
      };
    };
    systemd.services.homecompute-toolbox-memory-preflight = {
      description = "Refuse toolbox VM startup without host memory headroom";
      before = ["homecompute-toolbox-vm.service"];
      path = [pkgs.gawk];
      serviceConfig.Type = "oneshot";
      script = ''
        available_kib="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)"
        required_kib="$((${toString cfg.resources.memoryMiB} + 4096) * 1024))"
        if [ -z "$available_kib" ] || [ "$available_kib" -lt "$required_kib" ]; then
          echo "toolbox VM needs ${toString cfg.resources.memoryMiB} MiB plus 4 GiB host headroom; refusing startup" >&2
          exit 1
        fi
      '';
    };
  };
}
