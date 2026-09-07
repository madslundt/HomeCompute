{ ... }:
{
  # NetworkManager remains the sole owner of this NIC. This profile creates only
  # the connected route for the dedicated cable; it has no gateway or DNS.
  networking.networkmanager.ensureProfiles.profiles.homecompute-compute-link = {
    connection = {
      id = "homecompute-compute-link";
      type = "ethernet";
      interface-name = "enp45s0";
      autoconnect = true;
    };
    ipv4 = {
      method = "manual";
      address1 = "10.77.10.2/24";
      never-default = true;
      ignore-auto-dns = true;
    };
    ipv6.method = "disabled";
  };

  # The link is client-only. The stateful firewall permits replies to
  # home-core's connections but publishes no service to home-spark.
  networking.firewall.interfaces.enp45s0 = {
    allowedTCPPorts = [ ];
    allowedUDPPorts = [ ];
    allowedTCPPortRanges = [ ];
    allowedUDPPortRanges = [ ];
  };

  # Keep the dedicated compute subnet off every non-dedicated OUTPUT path and
  # the link out of every routed path, including Tailscale exit-node traffic.
  # Version the policy chain whenever its contents change. A new version is
  # populated while unreferenced, then attached before the prior chain is
  # detached. The OUTPUT guard is installed first and intentionally survives
  # firewall.service stop, so a reload/stop cannot expose bearer traffic to the
  # LAN default route.
  networking.firewall.extraCommands = ''
    iptables -w -C OUTPUT -d 10.77.10.0/24 ! -o enp45s0 -j REJECT 2>/dev/null ||
      iptables -w -I OUTPUT 1 -d 10.77.10.0/24 ! -o enp45s0 -j REJECT
    iptables -w -C FORWARD -d 10.77.10.0/24 ! -o enp45s0 -j REJECT 2>/dev/null ||
      iptables -w -I FORWARD 1 -d 10.77.10.0/24 ! -o enp45s0 -j REJECT
    ip6tables -w -C FORWARD -i enp45s0 -j REJECT 2>/dev/null ||
      ip6tables -w -I FORWARD 1 -i enp45s0 -j REJECT
    ip6tables -w -C FORWARD -o enp45s0 -j REJECT 2>/dev/null ||
      ip6tables -w -I FORWARD 1 -o enp45s0 -j REJECT

    if ! iptables -w -C FORWARD -i enp45s0 -j HC-COMPUTE-V1 2>/dev/null &&
      ! iptables -w -C FORWARD -o enp45s0 -j HC-COMPUTE-V1 2>/dev/null; then
      iptables -w -N HC-COMPUTE-V1 2>/dev/null || true
      iptables -w -F HC-COMPUTE-V1
      iptables -w -A HC-COMPUTE-V1 -s 172.28.200.3/32 -d 10.77.10.10/32 -o enp45s0 -p tcp -m multiport --dports 8000,8001,8002,8003,8004,10200 -m conntrack --ctstate NEW,ESTABLISHED -j ACCEPT
      iptables -w -A HC-COMPUTE-V1 -i enp45s0 -s 10.77.10.10/32 -d 172.28.200.3/32 -p tcp -m multiport --sports 8000,8001,8002,8003,8004,10200 -m conntrack --ctstate ESTABLISHED,RELATED -j ACCEPT
      iptables -w -A HC-COMPUTE-V1 -j REJECT
    fi
    iptables -w -C FORWARD -i enp45s0 -j HC-COMPUTE-V1 2>/dev/null ||
      iptables -w -I FORWARD 1 -i enp45s0 -j HC-COMPUTE-V1
    iptables -w -C FORWARD -o enp45s0 -j HC-COMPUTE-V1 2>/dev/null ||
      iptables -w -I FORWARD 1 -o enp45s0 -j HC-COMPUTE-V1

    while iptables -w -C FORWARD -i enp45s0 -j HC-COMPUTE 2>/dev/null; do
      iptables -w -D FORWARD -i enp45s0 -j HC-COMPUTE
    done
    while iptables -w -C FORWARD -o enp45s0 -j HC-COMPUTE 2>/dev/null; do
      iptables -w -D FORWARD -o enp45s0 -j HC-COMPUTE
    done
    if iptables -w -L HC-COMPUTE -n >/dev/null 2>&1; then
      iptables -w -F HC-COMPUTE
      iptables -w -X HC-COMPUTE
    fi
  '';

  # Docker forwarding is enabled only for the exact versioned compute policy.
  boot.kernel.sysctl = {
    "net.ipv4.conf.enp45s0.forwarding" = 1;
    "net.ipv4.conf.enp45s0.accept_redirects" = 0;
    "net.ipv4.conf.enp45s0.send_redirects" = 0;
  };
}
