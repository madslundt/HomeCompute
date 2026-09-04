{ pkgs, ... }:
{
  # Docker publications bypass the host INPUT firewall. Restrict n8n egress in
  # DOCKER-USER; editor publications in production.yaml bind only loopback,
  # this host's LAN address, and its Tailscale address. The bridge has no IPv6 configuration.
  systemd.services.homecompute-automation-network = {
    description = "n8n container egress policy";
    wantedBy = [ "multi-user.target" ];
    requiredBy = [ "docker.service" ];
    before = [ "docker.service" ];
    partOf = [ "docker.service" ];
    path = [ pkgs.iptables ];
    serviceConfig = {
      Type = "oneshot";
      RemainAfterExit = true;
    };
    script = ''
      # Install before Docker restores containers, avoiding an egress gap at boot.
      iptables -w -N DOCKER-USER 2>/dev/null || true
      # Block routed access to Aula from outside this host's automation bridge,
      # including direct container-IP access. Host loopback uses OUTPUT instead.
      iptables -w -C DOCKER-USER ! -i br-hc-n8n -d 172.28.201.3/32 -m conntrack ! --ctstate ESTABLISHED,RELATED -j REJECT 2>/dev/null || \
        iptables -w -I DOCKER-USER 1 ! -i br-hc-n8n -d 172.28.201.3/32 -m conntrack ! --ctstate ESTABLISHED,RELATED -j REJECT
      iptables -w -N HC-AUTOMATION 2>/dev/null || true
      iptables -w -F HC-AUTOMATION
      iptables -w -A HC-AUTOMATION -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
      iptables -w -A HC-AUTOMATION -o br-hc-n8n -d 172.28.201.3/32 -p tcp --dport 7878 -j RETURN
      iptables -w -A HC-AUTOMATION -d 192.168.30.30/32 -p tcp --dport 7878 -j RETURN
      for subnet in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16 127.0.0.0/8 224.0.0.0/4; do
        iptables -w -A HC-AUTOMATION -d "$subnet" -j REJECT
      done
      iptables -w -A HC-AUTOMATION -p tcp --dport 443 -j RETURN
      iptables -w -A HC-AUTOMATION -j REJECT
      iptables -w -C DOCKER-USER -i br-hc-n8n -j HC-AUTOMATION 2>/dev/null || \
        iptables -w -I DOCKER-USER 1 -i br-hc-n8n -j HC-AUTOMATION
    '';
  };
}
