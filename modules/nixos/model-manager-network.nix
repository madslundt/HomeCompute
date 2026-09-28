{ pkgs, ... }:
{
  # The model-manager SSH bridge has one fixed workload identity. It may open
  # SSH only to the Spark's management address; replies are stateful only.
  # Guards are installed before replacing the active chains and remain in
  # place if this unit is stopped while Docker is still running.
  systemd.services.homecompute-model-manager-network = {
    description = "Model manager Docker SSH egress policy";
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

      # Fail closed while installing or replacing the rules.
      iptables -w -I DOCKER-USER 1 -i br-hc-model-manager-ssh -j REJECT
      iptables -w -I DOCKER-USER 1 -o br-hc-model-manager-ssh -d 172.28.205.0/24 -j REJECT

      while iptables -w -C DOCKER-USER -i br-hc-model-manager-ssh -j HC-MODEL-MANAGER-SSH 2>/dev/null; do
        iptables -w -D DOCKER-USER -i br-hc-model-manager-ssh -j HC-MODEL-MANAGER-SSH
      done
      while iptables -w -C DOCKER-USER -o br-hc-model-manager-ssh -d 172.28.205.0/24 -j HC-MODEL-MANAGER-RETURN 2>/dev/null; do
        iptables -w -D DOCKER-USER -o br-hc-model-manager-ssh -d 172.28.205.0/24 -j HC-MODEL-MANAGER-RETURN
      done

      iptables -w -N HC-MODEL-MANAGER-SSH 2>/dev/null || true
      iptables -w -F HC-MODEL-MANAGER-SSH
      iptables -w -A HC-MODEL-MANAGER-SSH -s 172.28.205.2/32 -o enp44s0 -d 192.168.30.126/32 -p tcp --dport 22 -m conntrack --ctstate NEW,ESTABLISHED -j RETURN
      iptables -w -A HC-MODEL-MANAGER-SSH -j REJECT

      iptables -w -N HC-MODEL-MANAGER-RETURN 2>/dev/null || true
      iptables -w -F HC-MODEL-MANAGER-RETURN
      iptables -w -A HC-MODEL-MANAGER-RETURN -s 192.168.30.126/32 -d 172.28.205.2/32 -p tcp --sport 22 -m conntrack --ctstate ESTABLISHED -j RETURN
      iptables -w -A HC-MODEL-MANAGER-RETURN -s 192.168.30.126/32 -d 172.28.205.2/32 -m conntrack --ctstate RELATED -j RETURN
      iptables -w -A HC-MODEL-MANAGER-RETURN -j REJECT

      iptables -w -I DOCKER-USER 1 -o br-hc-model-manager-ssh -d 172.28.205.0/24 -j HC-MODEL-MANAGER-RETURN
      iptables -w -I DOCKER-USER 1 -i br-hc-model-manager-ssh -j HC-MODEL-MANAGER-SSH

      iptables -w -C HC-MODEL-MANAGER-SSH -s 172.28.205.2/32 -o enp44s0 -d 192.168.30.126/32 -p tcp --dport 22 -m conntrack --ctstate NEW,ESTABLISHED -j RETURN
      iptables -w -C HC-MODEL-MANAGER-SSH -j REJECT
      iptables -w -C HC-MODEL-MANAGER-RETURN -s 192.168.30.126/32 -d 172.28.205.2/32 -p tcp --sport 22 -m conntrack --ctstate ESTABLISHED -j RETURN
      iptables -w -C HC-MODEL-MANAGER-RETURN -s 192.168.30.126/32 -d 172.28.205.2/32 -m conntrack --ctstate RELATED -j RETURN
      iptables -w -C HC-MODEL-MANAGER-RETURN -j REJECT
      iptables -w -C DOCKER-USER -o br-hc-model-manager-ssh -d 172.28.205.0/24 -j HC-MODEL-MANAGER-RETURN
      iptables -w -C DOCKER-USER -i br-hc-model-manager-ssh -j HC-MODEL-MANAGER-SSH

      while iptables -w -C DOCKER-USER -i br-hc-model-manager-ssh -j REJECT 2>/dev/null; do
        iptables -w -D DOCKER-USER -i br-hc-model-manager-ssh -j REJECT
      done
      while iptables -w -C DOCKER-USER -o br-hc-model-manager-ssh -d 172.28.205.0/24 -j REJECT 2>/dev/null; do
        iptables -w -D DOCKER-USER -o br-hc-model-manager-ssh -d 172.28.205.0/24 -j REJECT
      done
    '';
    preStop = ''
      iptables -w -N DOCKER-USER 2>/dev/null || true
      iptables -w -I DOCKER-USER 1 -i br-hc-model-manager-ssh -j REJECT
      iptables -w -I DOCKER-USER 1 -o br-hc-model-manager-ssh -d 172.28.205.0/24 -j REJECT

      while iptables -w -C DOCKER-USER -i br-hc-model-manager-ssh -j HC-MODEL-MANAGER-SSH 2>/dev/null; do
        iptables -w -D DOCKER-USER -i br-hc-model-manager-ssh -j HC-MODEL-MANAGER-SSH
      done
      while iptables -w -C DOCKER-USER -o br-hc-model-manager-ssh -d 172.28.205.0/24 -j HC-MODEL-MANAGER-RETURN 2>/dev/null; do
        iptables -w -D DOCKER-USER -o br-hc-model-manager-ssh -d 172.28.205.0/24 -j HC-MODEL-MANAGER-RETURN
      done

      for chain in HC-MODEL-MANAGER-SSH HC-MODEL-MANAGER-RETURN; do
        if iptables -w -L "$chain" -n >/dev/null 2>&1; then
          iptables -w -F "$chain"
          iptables -w -X "$chain"
        fi
      done
    '';
  };
}
