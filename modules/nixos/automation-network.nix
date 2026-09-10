{ pkgs, ... }:
{
  # Docker publications bypass the host INPUT firewall. Restrict Caddy ingress
  # and n8n egress in DOCKER-USER; editor publications bind only explicit host
  # addresses. The application bridge has no IPv6 configuration.
  systemd.services.homecompute-automation-network = {
    description = "Docker application network policy";
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

      # Docker DNAT bypasses INPUT. Keep the Caddy publication fail-closed
      # while replacing its ingress policy, then permit only Tailscale traffic.
      iptables -w -I DOCKER-USER 1 -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 100.110.248.102 --ctorigdstport 443 -j REJECT
      while iptables -w -C DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 100.110.248.102 --ctorigdstport 443 -j HC-CADDY-INGRESS 2>/dev/null; do
        iptables -w -D DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 100.110.248.102 --ctorigdstport 443 -j HC-CADDY-INGRESS
      done
      iptables -w -N HC-CADDY-INGRESS 2>/dev/null || true
      iptables -w -F HC-CADDY-INGRESS
      iptables -w -A HC-CADDY-INGRESS -i tailscale0 -j RETURN
      iptables -w -A HC-CADDY-INGRESS -j REJECT
      iptables -w -I DOCKER-USER 1 -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 100.110.248.102 --ctorigdstport 443 -j HC-CADDY-INGRESS
      iptables -w -C HC-CADDY-INGRESS -i tailscale0 -j RETURN
      iptables -w -C HC-CADDY-INGRESS -j REJECT
      while iptables -w -C DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 100.110.248.102 --ctorigdstport 443 -j REJECT 2>/dev/null; do
        iptables -w -D DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 100.110.248.102 --ctorigdstport 443 -j REJECT
      done

      # The LAN publication is reachable from the trusted Default network and
      # through the advertised Tailscale subnet route. Other VLANs remain
      # fail-closed at the host even if upstream UniFi policy is broadened.
      iptables -w -I DOCKER-USER 1 -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 443 -j REJECT
      while iptables -w -C DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 443 -j HC-CADDY-LAN 2>/dev/null; do
        iptables -w -D DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 443 -j HC-CADDY-LAN
      done
      iptables -w -N HC-CADDY-LAN 2>/dev/null || true
      iptables -w -F HC-CADDY-LAN
      iptables -w -A HC-CADDY-LAN -i enp44s0 -s 192.168.10.0/24 -j RETURN
      iptables -w -A HC-CADDY-LAN -i tailscale0 -j RETURN
      iptables -w -A HC-CADDY-LAN -j REJECT
      iptables -w -I DOCKER-USER 1 -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 443 -j HC-CADDY-LAN
      iptables -w -C HC-CADDY-LAN -i enp44s0 -s 192.168.10.0/24 -j RETURN
      iptables -w -C HC-CADDY-LAN -i tailscale0 -j RETURN
      iptables -w -C HC-CADDY-LAN -j REJECT
      while iptables -w -C DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 443 -j REJECT 2>/dev/null; do
        iptables -w -D DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 443 -j REJECT
      done

      # Wyoming carries no client authentication. Only the Home Assistant
      # appliance may reach the Danish Piper publication on the home LAN.
      iptables -w -I DOCKER-USER 1 -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 10200 -j REJECT
      while iptables -w -C DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 10200 -j HC-PIPER-INGRESS 2>/dev/null; do
        iptables -w -D DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 10200 -j HC-PIPER-INGRESS
      done
      iptables -w -N HC-PIPER-INGRESS 2>/dev/null || true
      iptables -w -F HC-PIPER-INGRESS
      iptables -w -A HC-PIPER-INGRESS -i enp44s0 -s 192.168.30.30/32 -j RETURN
      iptables -w -A HC-PIPER-INGRESS -j REJECT
      iptables -w -I DOCKER-USER 1 -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 10200 -j HC-PIPER-INGRESS
      iptables -w -C HC-PIPER-INGRESS -i enp44s0 -s 192.168.30.30/32 -j RETURN
      iptables -w -C HC-PIPER-INGRESS -j REJECT
      while iptables -w -C DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 10200 -j REJECT 2>/dev/null; do
        iptables -w -D DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 10200 -j REJECT
      done

      # The bridge needs a gateway for Docker's host publication, so enforce
      # the offline runtime boundary explicitly in DOCKER-USER.
      iptables -w -I DOCKER-USER 1 -i br-hc-piper -j REJECT
      while iptables -w -C DOCKER-USER -i br-hc-piper -j HC-PIPER-EGRESS 2>/dev/null; do
        iptables -w -D DOCKER-USER -i br-hc-piper -j HC-PIPER-EGRESS
      done
      iptables -w -N HC-PIPER-EGRESS 2>/dev/null || true
      iptables -w -F HC-PIPER-EGRESS
      iptables -w -A HC-PIPER-EGRESS -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
      iptables -w -A HC-PIPER-EGRESS -j REJECT
      iptables -w -I DOCKER-USER 1 -i br-hc-piper -j HC-PIPER-EGRESS
      iptables -w -C HC-PIPER-EGRESS -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
      iptables -w -C HC-PIPER-EGRESS -j REJECT
      while iptables -w -C DOCKER-USER -i br-hc-piper -j REJECT 2>/dev/null; do
        iptables -w -D DOCKER-USER -i br-hc-piper -j REJECT
      done

      # Keep bridge egress and Aula ingress fail-closed while replacing the
      # live policy. Always add fresh guards: failures leave both protections.
      iptables -w -I DOCKER-USER 1 -i br-hc-n8n -j REJECT
      iptables -w -I DOCKER-USER 2 ! -i br-hc-n8n -d 172.28.201.3/32 -j REJECT

      while iptables -w -C DOCKER-USER -i br-hc-n8n -j HC-AUTOMATION 2>/dev/null; do
        iptables -w -D DOCKER-USER -i br-hc-n8n -j HC-AUTOMATION
      done
      while iptables -w -C DOCKER-USER ! -i br-hc-n8n -d 172.28.201.3/32 -m conntrack ! --ctstate ESTABLISHED,RELATED -j REJECT 2>/dev/null; do
        iptables -w -D DOCKER-USER ! -i br-hc-n8n -d 172.28.201.3/32 -m conntrack ! --ctstate ESTABLISHED,RELATED -j REJECT
      done

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

      # The temporary guard stays ahead of both live rules until the complete
      # replacement policy and its jump have been installed and verified.
      iptables -w -I DOCKER-USER 2 ! -i br-hc-n8n -d 172.28.201.3/32 -m conntrack ! --ctstate ESTABLISHED,RELATED -j REJECT
      iptables -w -I DOCKER-USER 2 -i br-hc-n8n -j HC-AUTOMATION

      iptables -w -C HC-AUTOMATION -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
      iptables -w -C HC-AUTOMATION -o br-hc-n8n -d 172.28.201.3/32 -p tcp --dport 7878 -j RETURN
      iptables -w -C HC-AUTOMATION -d 192.168.30.30/32 -p tcp --dport 7878 -j RETURN
      for subnet in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16 127.0.0.0/8 224.0.0.0/4; do
        iptables -w -C HC-AUTOMATION -d "$subnet" -j REJECT
      done
      iptables -w -C HC-AUTOMATION -p tcp --dport 443 -j RETURN
      iptables -w -C HC-AUTOMATION -j REJECT
      iptables -w -C DOCKER-USER ! -i br-hc-n8n -d 172.28.201.3/32 -m conntrack ! --ctstate ESTABLISHED,RELATED -j REJECT
      iptables -w -C DOCKER-USER -i br-hc-n8n -j HC-AUTOMATION

      while iptables -w -C DOCKER-USER -i br-hc-n8n -j REJECT 2>/dev/null; do
        iptables -w -D DOCKER-USER -i br-hc-n8n -j REJECT
      done
      while iptables -w -C DOCKER-USER ! -i br-hc-n8n -d 172.28.201.3/32 -j REJECT 2>/dev/null; do
        iptables -w -D DOCKER-USER ! -i br-hc-n8n -d 172.28.201.3/32 -j REJECT
      done
    '';

    preStop = ''
      iptables -w -N DOCKER-USER 2>/dev/null || true
      iptables -w -I DOCKER-USER 1 -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 100.110.248.102 --ctorigdstport 443 -j REJECT
      while iptables -w -C DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 100.110.248.102 --ctorigdstport 443 -j HC-CADDY-INGRESS 2>/dev/null; do
        iptables -w -D DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 100.110.248.102 --ctorigdstport 443 -j HC-CADDY-INGRESS
      done
      if iptables -w -L HC-CADDY-INGRESS -n >/dev/null 2>&1; then
        iptables -w -F HC-CADDY-INGRESS
        iptables -w -X HC-CADDY-INGRESS
      fi
      iptables -w -I DOCKER-USER 1 -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 443 -j REJECT
      while iptables -w -C DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 443 -j HC-CADDY-LAN 2>/dev/null; do
        iptables -w -D DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 443 -j HC-CADDY-LAN
      done
      if iptables -w -L HC-CADDY-LAN -n >/dev/null 2>&1; then
        iptables -w -F HC-CADDY-LAN
        iptables -w -X HC-CADDY-LAN
      fi
      iptables -w -I DOCKER-USER 1 -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 10200 -j REJECT
      while iptables -w -C DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 10200 -j HC-PIPER-INGRESS 2>/dev/null; do
        iptables -w -D DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 10200 -j HC-PIPER-INGRESS
      done
      if iptables -w -L HC-PIPER-INGRESS -n >/dev/null 2>&1; then
        iptables -w -F HC-PIPER-INGRESS
        iptables -w -X HC-PIPER-INGRESS
      fi
      iptables -w -I DOCKER-USER 1 -i br-hc-piper -j REJECT
      while iptables -w -C DOCKER-USER -i br-hc-piper -j HC-PIPER-EGRESS 2>/dev/null; do
        iptables -w -D DOCKER-USER -i br-hc-piper -j HC-PIPER-EGRESS
      done
      if iptables -w -L HC-PIPER-EGRESS -n >/dev/null 2>&1; then
        iptables -w -F HC-PIPER-EGRESS
        iptables -w -X HC-PIPER-EGRESS
      fi
      iptables -w -I DOCKER-USER 1 -i br-hc-n8n -j REJECT
      iptables -w -I DOCKER-USER 2 ! -i br-hc-n8n -d 172.28.201.3/32 -j REJECT

      while iptables -w -C DOCKER-USER -i br-hc-n8n -j HC-AUTOMATION 2>/dev/null; do
        iptables -w -D DOCKER-USER -i br-hc-n8n -j HC-AUTOMATION
      done
      while iptables -w -C DOCKER-USER ! -i br-hc-n8n -d 172.28.201.3/32 -m conntrack ! --ctstate ESTABLISHED,RELATED -j REJECT 2>/dev/null; do
        iptables -w -D DOCKER-USER ! -i br-hc-n8n -d 172.28.201.3/32 -m conntrack ! --ctstate ESTABLISHED,RELATED -j REJECT
      done

      if iptables -w -L HC-AUTOMATION -n >/dev/null 2>&1; then
        iptables -w -F HC-AUTOMATION
        iptables -w -X HC-AUTOMATION
      fi

      # Keep both guards installed if this unit stops while Docker is still
      # running. A successful restart removes duplicate guards only after the
      # complete live policy is active and verified.
    '';
  };
}
