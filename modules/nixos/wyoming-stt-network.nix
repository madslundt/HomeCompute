{ pkgs, ... }:
{
  # Docker-published Wyoming has no authentication. Install the policy before
  # Docker restores containers so neither ingress nor runtime egress has a gap.
  systemd.services.homecompute-wyoming-stt-network = {
    description = "Wyoming STT Docker network policy";
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
      iptables -w -N DOCKER-USER 2>/dev/null || true

      iptables -w -I DOCKER-USER 1 -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 10300 -j REJECT
      while iptables -w -C DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 10300 -j HC-STT-INGRESS 2>/dev/null; do
        iptables -w -D DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 10300 -j HC-STT-INGRESS
      done
      iptables -w -N HC-STT-INGRESS 2>/dev/null || true
      iptables -w -F HC-STT-INGRESS
      iptables -w -A HC-STT-INGRESS -i enp44s0 -s 192.168.30.30/32 -j RETURN
      iptables -w -A HC-STT-INGRESS -j REJECT
      iptables -w -I DOCKER-USER 1 -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 10300 -j HC-STT-INGRESS
      iptables -w -C HC-STT-INGRESS -i enp44s0 -s 192.168.30.30/32 -j RETURN
      iptables -w -C HC-STT-INGRESS -j REJECT
      while iptables -w -C DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 10300 -j REJECT 2>/dev/null; do
        iptables -w -D DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 10300 -j REJECT
      done

      iptables -w -I DOCKER-USER 1 -i br-hc-stt -j REJECT
      while iptables -w -C DOCKER-USER -i br-hc-stt -j HC-STT-EGRESS 2>/dev/null; do
        iptables -w -D DOCKER-USER -i br-hc-stt -j HC-STT-EGRESS
      done
      iptables -w -N HC-STT-EGRESS 2>/dev/null || true
      iptables -w -F HC-STT-EGRESS
      iptables -w -A HC-STT-EGRESS -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
      iptables -w -A HC-STT-EGRESS -j REJECT
      iptables -w -I DOCKER-USER 1 -i br-hc-stt -j HC-STT-EGRESS
      iptables -w -C HC-STT-EGRESS -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
      iptables -w -C HC-STT-EGRESS -j REJECT
      while iptables -w -C DOCKER-USER -i br-hc-stt -j REJECT 2>/dev/null; do
        iptables -w -D DOCKER-USER -i br-hc-stt -j REJECT
      done

      iptables -w -I DOCKER-USER 1 -i br-hc-sttfetch -j REJECT
      while iptables -w -C DOCKER-USER -i br-hc-sttfetch -j HC-STT-FETCH 2>/dev/null; do
        iptables -w -D DOCKER-USER -i br-hc-sttfetch -j HC-STT-FETCH
      done
      iptables -w -N HC-STT-FETCH 2>/dev/null || true
      iptables -w -F HC-STT-FETCH
      iptables -w -A HC-STT-FETCH -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
      iptables -w -A HC-STT-FETCH -p udp --dport 53 -j RETURN
      iptables -w -A HC-STT-FETCH -p tcp --dport 53 -j RETURN
      for subnet in 10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 100.64.0.0/10 169.254.0.0/16 127.0.0.0/8 224.0.0.0/4; do
        iptables -w -A HC-STT-FETCH -d "$subnet" -j REJECT
      done
      iptables -w -A HC-STT-FETCH -p tcp --dport 443 -j RETURN
      iptables -w -A HC-STT-FETCH -j REJECT
      iptables -w -I DOCKER-USER 1 -i br-hc-sttfetch -j HC-STT-FETCH
      iptables -w -C HC-STT-FETCH -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
      iptables -w -C HC-STT-FETCH -p udp --dport 53 -j RETURN
      iptables -w -C HC-STT-FETCH -p tcp --dport 443 -j RETURN
      iptables -w -C HC-STT-FETCH -j REJECT
      while iptables -w -C DOCKER-USER -i br-hc-sttfetch -j REJECT 2>/dev/null; do
        iptables -w -D DOCKER-USER -i br-hc-sttfetch -j REJECT
      done
    '';
    preStop = ''
      iptables -w -N DOCKER-USER 2>/dev/null || true
      iptables -w -I DOCKER-USER 1 -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 10300 -j REJECT
      while iptables -w -C DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 10300 -j HC-STT-INGRESS 2>/dev/null; do
        iptables -w -D DOCKER-USER -p tcp -m conntrack --ctdir ORIGINAL --ctorigdst 192.168.30.122 --ctorigdstport 10300 -j HC-STT-INGRESS
      done
      iptables -w -I DOCKER-USER 1 -i br-hc-stt -j REJECT
      while iptables -w -C DOCKER-USER -i br-hc-stt -j HC-STT-EGRESS 2>/dev/null; do
        iptables -w -D DOCKER-USER -i br-hc-stt -j HC-STT-EGRESS
      done
      iptables -w -I DOCKER-USER 1 -i br-hc-sttfetch -j REJECT
      while iptables -w -C DOCKER-USER -i br-hc-sttfetch -j HC-STT-FETCH 2>/dev/null; do
        iptables -w -D DOCKER-USER -i br-hc-sttfetch -j HC-STT-FETCH
      done
      for chain in HC-STT-INGRESS HC-STT-EGRESS HC-STT-FETCH; do
        if iptables -w -L "$chain" -n >/dev/null 2>&1; then
          iptables -w -F "$chain"
          iptables -w -X "$chain"
        fi
      done
    '';
  };
}
