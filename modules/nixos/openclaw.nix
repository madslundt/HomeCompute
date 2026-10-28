{ config, lib, pkgs, ... }:
let
  cfg = config.homecompute.openclaw;
  composeTree = pkgs.runCommand "homecompute-assistant-compose" { } ''
    mkdir -p $out/deploy
    cp -R ${../../deploy/openclaw} $out/deploy/openclaw
    cp -R ${../../deploy/codex-worker} $out/deploy/codex-worker
  '';
  compose = "${pkgs.docker}/bin/docker compose --env-file /etc/homecompute/openclaw.env --file ${composeTree}/deploy/openclaw/compose.yaml --profile assistant";
  secretNames = [ "gateway_token" "model_key" "broker_token" "worker_token" "operator_token" "github_token" "github_checkout_token" "codex_api_key" "snapshot_token" ];
in
{
  options.homecompute.openclaw = {
    enable = lib.mkEnableOption "isolated OpenClaw and Codex pilot (explicit operator approval required)";
    dataClassification = lib.mkOption {
      type = lib.types.enum [ "synthetic-only" "household" ];
      default = "synthetic-only";
      description = "Household rollout also requires an independently verified restore drill.";
    };
    restoreVerified = lib.mkOption { type = lib.types.bool; default = false; };
    projectsFile = lib.mkOption { type = lib.types.path; default = ../../config/codex-projects.json; };
    actionsFile = lib.mkOption { type = lib.types.path; default = ../../config/system-actions.json; };
    workDiskGiB = lib.mkOption { type = lib.types.ints.between 4 32; default = 12; };
    stateDiskGiB = lib.mkOption { type = lib.types.ints.between 2 8; default = 4; };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = config.homecompute.secrets.enable;
        message = "OpenClaw pilot requires external sops-nix credentials, never Nix-store secrets";
      }
      {
        assertion = cfg.dataClassification == "synthetic-only" ||
          (config.homecompute.backups.enable && cfg.restoreVerified);
        message = "Household OpenClaw needs off-host backup and a verified restore";
      }
    ];

    users.groups.homecompute-assistant-secrets = { gid = 992; };
    sops.secrets = lib.genAttrs (map (name: "openclaw/${name}") secretNames) (name: {
      group = if lib.elem name [ "openclaw/codex_api_key" "openclaw/github_checkout_token" ] then "root" else "homecompute-assistant-secrets";
      mode = if lib.elem name [ "openclaw/codex_api_key" "openclaw/github_checkout_token" ] then "0400" else "0440";
    });
    environment.etc."homecompute/openclaw/openclaw.json" = {
      source = ../../config/openclaw.json;
      group = "homecompute-assistant-secrets";
      mode = "0440";
    };
    environment.etc."homecompute/openclaw/projects.json" = { source = cfg.projectsFile; mode = "0444"; };
    environment.etc."homecompute/openclaw/actions.json" = { source = cfg.actionsFile; mode = "0444"; };
    environment.etc."homecompute/openclaw/monitoring.json" = { source = ../../config/system-monitoring.json; mode = "0444"; };
    environment.etc."homecompute/openclaw.env" = {
      mode = "0600";
      text = lib.replaceStrings [ "OPENCLAW_SECRET_GID=\n" ] [ "OPENCLAW_SECRET_GID=992\n" ]
        (builtins.readFile ../../config/openclaw.env.example);
    };
    systemd.tmpfiles.rules = [
      "d /srv/state/openclaw 0700 root root - -"
      "d /srv/state/openclaw-work 0711 root root - -"
    ];
    # A separate ext4 loop filesystem bounds accumulated worktrees/logs.
    # Compose mem_limit does not bound writes to a bind-mounted directory.
    systemd.services.homecompute-openclaw-work-disk = {
      before = [ "srv-state-openclaw.mount" "srv-state-openclaw\\x2dwork.mount" ];
      serviceConfig = { Type = "oneshot"; RemainAfterExit = true; };
      path = [ pkgs.coreutils pkgs.e2fsprogs ];
      script = ''
        umask 0077
        for item in openclaw:${toString cfg.stateDiskGiB} openclaw-work:${toString cfg.workDiskGiB}; do
          name="''${item%:*}"
          size="''${item#*:}"
          image="/srv/state/$name.img"
          if [ ! -e "$image" ]; then
            truncate -s "''${size}G" "$image.new"
            mkfs.ext4 -q "$image.new"
            mv "$image.new" "$image"
          fi
        done
      '';
    };
    fileSystems."/srv/state/openclaw-work" = {
      device = "/srv/state/openclaw-work.img";
      fsType = "ext4";
      options = [ "loop" "nosuid" "nodev" "noauto" "x-systemd.requires=homecompute-openclaw-work-disk.service" ];
    };
    fileSystems."/srv/state/openclaw" = {
      device = "/srv/state/openclaw.img";
      fsType = "ext4";
      options = [ "loop" "nosuid" "nodev" "noauto" "x-systemd.requires=homecompute-openclaw-work-disk.service" ];
    };
    systemd.services.homecompute-openclaw = {
      description = "Isolated OpenClaw and single Codex worker pilot";
      wantedBy = [ "multi-user.target" ];
      requires = [ "docker.service" ];
      wants = [ "network-online.target" ];
      after = [ "docker.service" "sops-nix.service" "network-online.target" ];
      unitConfig.RequiresMountsFor = [ "/srv/state/openclaw" "/srv/state/openclaw-work" ];
      serviceConfig = {
        Type = "oneshot";
        RemainAfterExit = true;
        TimeoutStartSec = "15min";
        ExecStopPost = "${pkgs.writeShellScript "assistant-failed-start-cleanup" ''
          ${compose} down --timeout 15
        ''}";
      };
      preStart = ''
        ${pkgs.coreutils}/bin/chown root:root /srv/state/openclaw-work
        ${pkgs.coreutils}/bin/chmod 0711 /srv/state/openclaw-work
        ${pkgs.coreutils}/bin/install -d -m 0700 -o 1000 -g 1000 \
          /srv/state/openclaw/openclaw /srv/state/openclaw/openclaw/workspace /srv/state/openclaw/broker
        # Marker is persistent and stops restart/autostart after an emergency.
        test ! -e /srv/state/openclaw/DISABLED
      '';
      script = ''
        ${compose} config --quiet
        ${compose} up --detach --build --wait --wait-timeout 180
      '';
      preStop = ''
        ${compose} down --timeout 15
      '';
    };
    # Fold into the existing pre-Docker policy lifecycle so it survives Docker
    # restart and starts before restart:unless-stopped containers are restored.
    systemd.services.homecompute-automation-network.script = lib.mkAfter ''
      iptables -w -I DOCKER-USER 1 -i br-hc-assist -j REJECT
      iptables -w -N HC-ASSISTANT-EGRESS 2>/dev/null || true
      iptables -w -F HC-ASSISTANT-EGRESS
      iptables -w -A HC-ASSISTANT-EGRESS -m conntrack --ctstate ESTABLISHED,RELATED -j RETURN
      iptables -w -A HC-ASSISTANT-EGRESS -s 172.29.210.2/32 -d 172.28.200.2/32 -p tcp --dport 8443 -j RETURN
      iptables -w -A HC-ASSISTANT-EGRESS ! -s 172.29.210.3/32 -j REJECT
      for subnet in 0.0.0.0/8 10.0.0.0/8 100.64.0.0/10 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.168.0.0/16 224.0.0.0/4; do
        iptables -w -A HC-ASSISTANT-EGRESS -d "$subnet" -j REJECT
      done
      iptables -w -A HC-ASSISTANT-EGRESS -p tcp --dport 443 -j RETURN
      iptables -w -A HC-ASSISTANT-EGRESS -j REJECT
      iptables -w -I HC-CADDY-LAN 1 -i br-hc-assist -s 172.29.210.2/32 -j RETURN
      iptables -w -C DOCKER-USER -i br-hc-assist -j HC-ASSISTANT-EGRESS 2>/dev/null || \
        iptables -w -I DOCKER-USER 1 -i br-hc-assist -j HC-ASSISTANT-EGRESS
      while iptables -w -C DOCKER-USER -i br-hc-assist -j REJECT 2>/dev/null; do
        iptables -w -D DOCKER-USER -i br-hc-assist -j REJECT
      done
      ip6tables -w -C DOCKER-USER -i br-hc-assist -j REJECT 2>/dev/null || \
        ip6tables -w -I DOCKER-USER 1 -i br-hc-assist -j REJECT
      iptables -w -C INPUT -i br-hc-assist -j REJECT 2>/dev/null || \
        iptables -w -I INPUT 1 -i br-hc-assist -j REJECT
    '';
    # Preserve the existing backup destination contract. Stop the pilot for an
    # application-consistent backup of memory and SQLite WAL plus workspace disk.
    homecompute.backups.sources.openclaw = lib.mkIf config.homecompute.backups.enable {
      destinations = [ "hetzner" ];
      paths = [ "/srv/state/openclaw" "/srv/state/openclaw-work" ];
      prepareCommand = ''
        # Actual Docker state also catches failed systemd startup with survivors.
        running="$(${compose} ps --quiet --status running)" || exit 1
        if [ -n "$running" ]; then
          echo "Stop all assistant containers for a consistent backup" >&2
          exit 1
        fi
      '';
      schedule = "daily";
    };
  };
}
