{ pkgs, ... }:
let
  metricDirectory = "/var/lib/homecompute-health";
  computeKeyFile = "/run/secrets/control-plane/compute-api-key";
  edgeCA = "/srv/state/control-plane/caddy-data/caddy/pki/authorities/local/root.crt";
  wyomingHealthProbe = pkgs.writeShellScript "homecompute-wyoming-health" ''
    set -eu
    exec 3<>/dev/tcp/10.77.10.10/10200
    printf '{"type":"describe","version":"1.0.0"}\n' >&3
    IFS= read -r -n 65537 event <&3
    ((''${#event} <= 65536)) || exit 1
    case "$event" in
      *'"type": "info"'*|*'"type":"info"'*) exit 0 ;;
      *) exit 1 ;;
    esac
  '';


  platformHealthProbe = pkgs.writeShellApplication {
    name = "homecompute-platform-health";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.curl
    ];
    text = ''
      metric_tmp="$(mktemp "${metricDirectory}/.homecompute-health.prom.XXXXXX")"
      auth_header="$RUNTIME_DIRECTORY/compute-auth.header"
      trap 'rm -f "$metric_tmp" "$auth_header"' EXIT

      edge_healthy=0
      n8n_ready=0
      gx10_ready=0
      gx10_text_ready=0
      gx10_embedding_ready=0
      gx10_vision_ready=0
      gx10_stt_ready=0
      gx10_tts_ready=0
      gx10_wyoming_ready=0
      # This is deliberately only a process/edge probe. The route is the
      # documented unauthenticated Caddy health endpoint; model health is
      # measured separately below. The repository CA, hostname, and destination
      # address are all pinned for this local check.
      if curl \
        --silent \
        --fail \
        --output /dev/null \
        --connect-timeout 2 \
        --max-time 5 \
        --proto '=https' \
        --cacert '${edgeCA}' \
        --resolve 'home-core.tail479ad.ts.net:443:100.110.248.102' \
        'https://home-core.tail479ad.ts.net/healthz'; then
        edge_healthy=1
      else
        echo 'homecompute edge process health probe failed' >&2
      fi

      if curl \
        --silent \
        --fail \
        --output /dev/null \
        --connect-timeout 2 \
        --max-time 5 \
        --proto '=http' \
        'http://127.0.0.1:15678/healthz/readiness'; then
        n8n_ready=1
      else
        echo 'homecompute n8n readiness probe failed' >&2
      fi

      if [ -r '${computeKeyFile}' ]; then
        compute_api_key="$(tr -d '\r\n' < '${computeKeyFile}')"
        if [ -n "$compute_api_key" ]; then
          umask 077
          printf 'Authorization: Bearer %s\n' "$compute_api_key" > "$auth_header"
          if curl --silent --fail --output /dev/null --connect-timeout 2 --max-time 5 \
            --proto '=http' --header "@$auth_header" 'http://10.77.10.10:8000/v1/models'; then
            gx10_text_ready=1
          fi
        fi
      fi
      if curl --silent --fail --output /dev/null --connect-timeout 2 --max-time 5 \
        --proto '=http' 'http://10.77.10.10:8001/health'; then gx10_embedding_ready=1; fi
      if curl --silent --fail --output /dev/null --connect-timeout 2 --max-time 5 \
        --proto '=http' 'http://10.77.10.10:8002/health'; then gx10_vision_ready=1; fi
      if curl --silent --fail --output /dev/null --connect-timeout 2 --max-time 5 \
        --proto '=http' 'http://10.77.10.10:8003/health'; then gx10_stt_ready=1; fi
      if curl --silent --fail --output /dev/null --connect-timeout 2 --max-time 5 \
        --proto '=http' 'http://10.77.10.10:8004/health/ready'; then gx10_tts_ready=1; fi
      if timeout 5 '${wyomingHealthProbe}'; then
        gx10_wyoming_ready=1
      fi
      # Text is the baseline deployed service. The other gauges describe staged
      # profiles independently, so intentionally disabled candidates do not make
      # the whole platform unhealthy.
      if [ "$gx10_text_ready" -eq 1 ]; then
        gx10_ready=1
      else
        echo 'homecompute GX10 baseline text readiness probe failed' >&2
      fi

      # Fixed, label-free gauges keep cardinality and logged metadata bounded.
      # Probe bodies, model lists, credentials, and request IDs are never
      # written to the textfile collector or the journal.
      cat > "$metric_tmp" <<EOF
      # HELP homecompute_edge_process_healthy Caddy edge health endpoint returned success.
      # TYPE homecompute_edge_process_healthy gauge
      homecompute_edge_process_healthy $edge_healthy
      # HELP homecompute_n8n_ready n8n readiness endpoint returned success.
      # TYPE homecompute_n8n_ready gauge
      homecompute_n8n_ready $n8n_ready
      # HELP homecompute_gx10_model_ready The baseline GX10 text service returned ready; staged modality readiness is reported separately.
      # TYPE homecompute_gx10_model_ready gauge
      homecompute_gx10_model_ready $gx10_ready
      # HELP homecompute_gx10_text_ready Authenticated GX10 text model listing returned success.
      # TYPE homecompute_gx10_text_ready gauge
      homecompute_gx10_text_ready $gx10_text_ready
      # HELP homecompute_gx10_embedding_ready GX10 embedding health returned success.
      # TYPE homecompute_gx10_embedding_ready gauge
      homecompute_gx10_embedding_ready $gx10_embedding_ready
      # HELP homecompute_gx10_vision_ready GX10 vision health returned success.
      # TYPE homecompute_gx10_vision_ready gauge
      homecompute_gx10_vision_ready $gx10_vision_ready
      # HELP homecompute_gx10_stt_ready GX10 speech-to-text health returned success.
      # TYPE homecompute_gx10_stt_ready gauge
      homecompute_gx10_stt_ready $gx10_stt_ready
      # HELP homecompute_gx10_tts_ready GX10 OpenAI speech adapter readiness returned success.
      # TYPE homecompute_gx10_tts_ready gauge
      homecompute_gx10_tts_ready $gx10_tts_ready
      # HELP homecompute_gx10_wyoming_ready GX10 Wyoming TTS Describe returned service information.
      # TYPE homecompute_gx10_wyoming_ready gauge
      homecompute_gx10_wyoming_ready $gx10_wyoming_ready
      EOF
      chmod 0644 "$metric_tmp"
      mv -f "$metric_tmp" '${metricDirectory}/homecompute-health.prom'

      exit 0
    '';
  };
in
{
  systemd.tmpfiles.rules = [
    "d ${metricDirectory} 0755 root root - -"
  ];

  services.prometheus.exporters.node = {
    enable = true;
    listenAddress = "127.0.0.1";
    port = 9100;
    openFirewall = false;
    enabledCollectors = [
      "systemd"
      "textfile"
    ];
    extraFlags = [
      "--collector.textfile.directory=${metricDirectory}"
    ];
  };

  systemd.services.homecompute-platform-health = {
    description = "Probe HomeCompute platform health metadata";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${platformHealthProbe}/bin/homecompute-platform-health";
      TimeoutStartSec = "50s";
      RuntimeDirectory = "homecompute-platform-health";
      RuntimeDirectoryMode = "0700";
      UMask = "0022";

      AmbientCapabilities = "";
      CapabilityBoundingSet = "";
      DevicePolicy = "closed";
      IPAddressDeny = "any";
      IPAddressAllow = [
        "127.0.0.1/8"
        "100.110.248.102/32"
        "10.77.10.10/32"
        # cgroup IP filtering observes Docker's post-DNAT destination rather
        # than the loopback/Tailscale address used by curl.
        "172.28.200.0/24"
        "172.28.201.0/24"
      ];
      LockPersonality = true;
      MemoryDenyWriteExecute = true;
      MemoryMax = "128M";
      NoNewPrivileges = true;
      PrivateDevices = true;
      PrivateTmp = true;
      ProtectClock = true;
      ProtectControlGroups = true;
      ProtectHome = true;
      ProtectHostname = true;
      ProtectKernelLogs = true;
      ReadOnlyPaths = [
        "-${computeKeyFile}"
        "-${edgeCA}"
      ];
      ProtectKernelTunables = true;
      ProtectProc = "invisible";
      ProtectSystem = "strict";
      ReadWritePaths = [ metricDirectory ];
      RestrictAddressFamilies = [
        "AF_INET"
        "AF_INET6"
      ];
      RestrictNamespaces = true;
      RestrictRealtime = true;
      RestrictSUIDSGID = true;
      SystemCallArchitectures = "native";
      SystemCallFilter = [
        "@system-service"
        "~@privileged"
      ];
    };
  };

  systemd.timers.homecompute-platform-health = {
    description = "Periodically probe HomeCompute platform health";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnBootSec = "90s";
      OnUnitActiveSec = "60s";
      AccuracySec = "5s";
      RandomizedDelaySec = "10s";
      Unit = "homecompute-platform-health.service";
    };
  };
}
