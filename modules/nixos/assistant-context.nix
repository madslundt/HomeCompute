{ config, lib, pkgs, ... }:
let
  cfg = config.homecompute.assistantContext;
  contextPort = 18826;
  source = pkgs.runCommand "homecompute-assistant-context-source" { } ''
    mkdir -p $out/scripts $out/config
    ${lib.concatMapStringsSep "\n" (name: "cp ${../../scripts + "/${name}"} $out/scripts/${name}") [
      "assistant-context-server.py"
      "assistant_context_api.py"
      "assistant_context_store.py"
      "assistant_context_tools.py"
      "assistant_attention.py"
      "assistant_context_delivery.py"
      "assistant_context_briefings.py"
      "assistant_context_retrieval.py"
      "assistant_context_delegation.py"
      "assistant_context_recovery.py"
      "assistant_context_improvements.py"
      "embedding_client.py"
      "semantic_retrieval.py"
    ]}
    cp ${../../config/assistant-attention.json} $out/config/assistant-attention.json
    cp ${../../config/assistant-briefings.json} $out/config/assistant-briefings.json
    cp ${../../config/assistant-delegation.json} $out/config/assistant-delegation.json
  '';
  inferenceEnabled = cfg.inference.embedding || cfg.inference.briefings;
  gatewayHosts = pkgs.writeText "assistant-context-hosts" ''
    127.0.0.1 localhost
    ${cfg.inference.address} ai.home.arpa
  '';
  policy = pkgs.writeText "assistant-attention.json" (builtins.toJSON (
    (builtins.fromJSON (builtins.readFile ../../config/assistant-attention.json)) // { mode = cfg.mode; }
  ));
  briefings = if cfg.managedBriefings then "/var/lib/homecompute-assistant-policy/assistant-briefings.json"
    else "${source}/config/assistant-briefings.json";
  externalPath = value: lib.hasPrefix "/" value && !(lib.hasPrefix "/nix/store/" value);
in
{
  options.homecompute.assistantContext = {
    enable = lib.mkEnableOption "private proactive-assistant context (initially shadow only)";
    mode = lib.mkOption { type = lib.types.enum [ "shadow" "active" ]; default = "shadow"; };
    managedBriefings = lib.mkOption {
      type = lib.types.bool; default = false;
      description = "Read root-owned policy initialized and reconciled by setup-assistant-context-policy.py; never let the model write policy.";
    };
    jobsSourceDirectory = lib.mkOption {
      type = lib.types.str; default = "/srv/homecompute/current";
      description = "Root-owned published HomeCompute release used by opt-in source sync and improvement jobs.";
    };
    sync.enable = lib.mkEnableOption "periodic bounded context source scans as the existing source-owning identity";
    sync.caFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str; default = null;
      description = "Optional externally provisioned public Daythread CA bundle for this collector only.";
    };
    improvements = {
      enable = lib.mkEnableOption "standing-grant improvement proposals and reconciliation";
      githubTokenFile = lib.mkOption { type = lib.types.str; default = "/etc/homecompute/assistant-improvement-github-token"; };
      healthTokenFile = lib.mkOption { type = lib.types.str; default = "/etc/homecompute/assistant-context-policy-health-token"; };
      applyReviewed = lib.mkOption {
        type = lib.types.bool; default = false;
        description = "Apply only independently tested, reviewed and reconciled briefing policy with the guarded installer.";
      };
    };
    inference = {
      embedding = lib.mkEnableOption "authenticated local Harrier retrieval";
      briefings = lib.mkEnableOption "bounded local briefing interpretations";
      address = lib.mkOption {
        type = lib.types.strMatching "(10\\.[0-9]+\\.[0-9]+\\.[0-9]+|192\\.168\\.[0-9]+\\.[0-9]+|172\\.(1[6-9]|2[0-9]|3[01])\\.[0-9]+\\.[0-9]+)";
        default = "192.168.30.122";
        description = "Pinned private IPv4 of ai.home.arpa; installed in this service's private hosts file.";
      };
      embeddingCredentialFile = lib.mkOption { type = lib.types.str; default = "/etc/homecompute/assistant-context-embedding.json"; };
      briefingCredentialFile = lib.mkOption { type = lib.types.str; default = "/etc/homecompute/assistant-context-briefing.json"; };
      caFile = lib.mkOption {
        type = lib.types.str; default = "/srv/state/control-plane/caddy-data/caddy/pki/authorities/local/root.crt";
        description = "Existing private gateway's public CA, projected read-only for verified local TLS.";
      };
    };
    delegation = {
      enable = lib.mkEnableOption "qualified isolated OpenClaw worker";
      identityFile = lib.mkOption { type = lib.types.str; default = "/etc/homecompute/assistant-context-worker/id_ed25519"; };
      knownHostsFile = lib.mkOption { type = lib.types.str; default = "/etc/homecompute/assistant-context-worker/known_hosts"; };
    };
    delivery = {
      enable = lib.mkEnableOption "qualified owner-DM standing delivery";
      authFile = lib.mkOption { type = lib.types.str; default = "/etc/homecompute-assistant-context/delivery-auth.json"; };
    };
    credentialsFile = lib.mkOption {
      type = lib.types.str;
      default = "/etc/homecompute/assistant-context-clients.json";
      description = "External root-only client credentials, copied with systemd LoadCredential; never a Nix-store secret.";
    };
    dataClassification = lib.mkOption {
      type = lib.types.enum [ "synthetic-only" "household" ];
      default = "synthetic-only";
    };
    restoreVerified = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "An actual isolated restore of this service's state and credentials has been verified.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.sync.caFile == null || externalPath cfg.sync.caFile;
        message = "Assistant collector CA must be an external absolute file when configured.";
      }
      {
        assertion = builtins.all externalPath [ cfg.credentialsFile cfg.inference.embeddingCredentialFile
          cfg.inference.briefingCredentialFile cfg.inference.caFile cfg.delegation.identityFile cfg.delegation.knownHostsFile cfg.delivery.authFile cfg.jobsSourceDirectory cfg.improvements.githubTokenFile cfg.improvements.healthTokenFile ];
        message = "Assistant context credentials must be an external absolute path, never a Nix-store value.";
      }
      {
        assertion = !cfg.improvements.applyReviewed || (cfg.improvements.enable && cfg.managedBriefings);
        message = "Reviewed automatic policy apply requires the improvement controller and initialized managed briefing policy.";
      }
      {
        assertion = !cfg.delivery.enable || cfg.mode == "active";
        message = "Assistant context delivery requires an explicitly active policy and externally qualified standing grant.";
      }
      {
        assertion = cfg.dataClassification == "synthetic-only" ||
          (cfg.restoreVerified && config.homecompute.nasRecoveryBackup.enable);
        message = "Household context requires off-host state coverage and an isolated restore; the NAS marker alone is insufficient.";
      }
    ];
    users.groups.homecompute-context = { };
    users.users.homecompute-context = {
      isSystemUser = true;
      group = "homecompute-context";
      home = "/srv/state/assistant-context";
    };
    systemd.tmpfiles.rules = [
      "a+ /srv/state - - - - u:homecompute-context:--x"
      "d /srv/state/assistant-context 0700 homecompute-context homecompute-context - -"
    ] ++ lib.optional cfg.sync.enable "d /var/lib/homecompute-assistant 0700 homecompute-openclaw homecompute-openclaw - -"
      ++ lib.optional cfg.improvements.enable "d /var/lib/homecompute-assistant-policy-controller 0700 root root - -";
    systemd.services.homecompute-assistant-context-sync = lib.mkIf cfg.sync.enable {
      description = "Bounded Home Assistant, Aula, Calendar and Daythread context scans";
      after = [ "homecompute-assistant-context.service" "network-online.target" ];
      requires = [ "homecompute-assistant-context.service" ];
      wants = [ "network-online.target" ];
      unitConfig.ConditionPathExists = "/var/lib/homecompute-assistant/context-sync.json";
      environment.PYTHONDONTWRITEBYTECODE = "1";
      environment.SSL_CERT_FILE = lib.mkIf (cfg.sync.caFile != null) "%d/daythread-ca";
      serviceConfig = {
        Type = "oneshot"; User = "homecompute-openclaw"; Group = "homecompute-openclaw"; UMask = "0077";
        LoadCredential = lib.optional (cfg.sync.caFile != null) "daythread-ca:${cfg.sync.caFile}";
        ExecStart = "${pkgs.python3}/bin/python3 ${cfg.jobsSourceDirectory}/scripts/assistant-context-sync.py";
        NoNewPrivileges = true; PrivateTmp = true; ProtectSystem = "strict"; ProtectHome = true;
        ReadWritePaths = [ "/var/lib/homecompute-assistant" ];
        TimeoutStartSec = "5min"; MemoryMax = "384M";
      };
    };
    systemd.timers.homecompute-assistant-context-sync = lib.mkIf cfg.sync.enable {
      wantedBy = [ "timers.target" ];
      timerConfig = { OnBootSec = "3min"; OnUnitInactiveSec = "15min"; RandomizedDelaySec = "30s"; };
    };
    systemd.services.homecompute-assistant-context-improvements = lib.mkIf cfg.improvements.enable {
      description = "Independently evaluate assistant policy proposals under a standing grant";
      after = [ "homecompute-assistant-context.service" "network-online.target" ];
      wants = [ "network-online.target" ];
      unitConfig.ConditionPathExists = "/var/lib/homecompute-assistant-policy-controller/standing-grant.json";
      path = [ pkgs.python3 pkgs.git pkgs.gh pkgs.openssh pkgs.docker pkgs.bash pkgs.jq pkgs.ripgrep pkgs.shellcheck ];
      environment = { PYTHONDONTWRITEBYTECODE = "1"; HOME = "/var/lib/homecompute-assistant-policy-controller"; };
      serviceConfig = {
        Type = "oneshot"; User = "root"; UMask = "0077";
        LoadCredential = [ "github-token:${cfg.improvements.githubTokenFile}" "policy-health-token:${cfg.improvements.healthTokenFile}" ];
        ExecStart = "${pkgs.python3}/bin/python3 ${cfg.jobsSourceDirectory}/scripts/assistant-context-improve.py sweep --enable-controller"
          + lib.optionalString cfg.improvements.applyReviewed " --apply-reviewed";
        NoNewPrivileges = true; PrivateTmp = true; ProtectHome = true;
        # Fixed validation uses isolated checkouts and containerized Nix on hosts
        # without the command; this trusted operator is separate from the model.
        TimeoutStartSec = "30min"; MemoryMax = "2G";
      };
    };
    systemd.timers.homecompute-assistant-context-improvements = lib.mkIf cfg.improvements.enable {
      wantedBy = [ "timers.target" ];
      timerConfig = { OnBootSec = "20min"; OnUnitInactiveSec = "6h"; RandomizedDelaySec = "5min"; };
    };
    systemd.services.homecompute-assistant-context = {
      description = "Private source-linked assistant context and shadow attention";
      wantedBy = [ "multi-user.target" ];
      after = [ "network.target" ];
      unitConfig.RequiresMountsFor = [ "/srv/state/assistant-context" ];
      environment.PYTHONDONTWRITEBYTECODE = "1";
      path = lib.optional cfg.delegation.enable pkgs.openssh;
      environment.SSL_CERT_FILE = lib.mkIf inferenceEnabled "%d/inference-ca";
      serviceConfig = {
        User = "homecompute-context";
        Group = "homecompute-context";
        UMask = "0077";
        LoadCredential = [ "clients:${cfg.credentialsFile}" ]
          ++ lib.optional inferenceEnabled "inference-ca:${cfg.inference.caFile}"
          ++ lib.optional cfg.inference.embedding "embedding:${cfg.inference.embeddingCredentialFile}"
          ++ lib.optional cfg.inference.briefings "briefing:${cfg.inference.briefingCredentialFile}"
          ++ lib.optionals cfg.delegation.enable [ "openclaw_worker_ssh_key:${cfg.delegation.identityFile}" "openclaw_worker_known_hosts:${cfg.delegation.knownHostsFile}" ]
          ++ lib.optional cfg.delivery.enable "delivery:${cfg.delivery.authFile}";
        ExecStart = "${pkgs.python3}/bin/python3 ${source}/scripts/assistant-context-server.py --state /srv/state/assistant-context --clients %d/clients --policy ${policy} --briefings ${briefings} --port ${toString contextPort}"
          + lib.optionalString cfg.inference.embedding " --embedding-credential %d/embedding"
          + lib.optionalString cfg.inference.briefings " --briefing-credential %d/briefing"
          + lib.optionalString cfg.delegation.enable " --enable-delegation --delegation-config ${source}/config/assistant-delegation.json"
          + lib.optionalString cfg.delivery.enable " --enable-delivery --delivery-auth %d/delivery"
          + lib.optionalString (cfg.managedBriefings || cfg.improvements.enable) " --improvement-status";
        Restart = "on-failure";
        RestartSec = 5;
        NoNewPrivileges = true;
        CapabilityBoundingSet = "";
        PrivateTmp = true;
        PrivateDevices = true;
        ProtectSystem = "strict";
        ProtectHome = true;
        ProtectKernelTunables = true;
        ProtectKernelModules = true;
        ProtectControlGroups = true;
        RestrictSUIDSGID = true;
        RestrictAddressFamilies = [ "AF_UNIX" "AF_INET" ];
        IPAddressDeny = "any";
        IPAddressAllow = [ "127.0.0.1/32" ]
          # Docker DNATs the gateway's published HTTPS port to its fixed Caddy
          # edge address. The cgroup filter also sees that translated endpoint.
          ++ lib.optionals inferenceEnabled [ "${cfg.inference.address}/32" "172.28.200.2/32" ]
          ++ lib.optional (cfg.delegation.enable || cfg.delivery.enable) "10.77.20.2/32";
        BindReadOnlyPaths = lib.optional inferenceEnabled "${gatewayHosts}:/etc/hosts";
        ReadWritePaths = [ "/srv/state/assistant-context" ];
        MemoryMax = "256M";
        TasksMax = 32;
      };
    };
  };
}
