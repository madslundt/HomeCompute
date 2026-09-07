{ pkgs, ... }:
let
  pinsFile = "/etc/homecompute/model-update-pins.json";
  selectionFile = "/var/lib/homecompute/benchmarks/selection.json";
  reportFile = "/var/lib/homecompute/model-update-check/report.json";
  notificationEnvironment = "/etc/homecompute/model-update-notification.env";

  monitorBundle = pkgs.runCommand "homecompute-model-update-monitor" { } ''
    install -Dm0555 ${../../scripts/check-model-updates.py} \
      "$out/libexec/homecompute-model-update-monitor/check-model-updates.py"
    install -Dm0444 ${../../scripts/model_update_selection.py} \
      "$out/libexec/homecompute-model-update-monitor/model_update_selection.py"
    install -Dm0444 ${../../scripts/update_check_http.py} \
      "$out/libexec/homecompute-model-update-monitor/update_check_http.py"
    install -Dm0444 ${../../automations/update-check/watchlist.json} \
      "$out/share/homecompute-model-update-monitor/watchlist.json"
  '';

  runMonitor = pkgs.writeShellApplication {
    name = "homecompute-model-update-monitor";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.python3
    ];
    text = ''
      # STATE_DIRECTORY is injected by systemd for StateDirectory=.
      # shellcheck disable=SC2153
      state_directory="$(readlink -f -- "$STATE_DIRECTORY")"
      arguments=(
        --watchlist '${monitorBundle}/share/homecompute-model-update-monitor/watchlist.json'
        --state "$state_directory/state.json"
        --report "$state_directory/report.json"
      )

      if [ -e '${pinsFile}' ]; then
        arguments+=(--pins '${pinsFile}')
      fi
      if [ -e '${selectionFile}' ]; then
        arguments+=(--selection '${selectionFile}')
      fi

      exec python3 \
        '${monitorBundle}/libexec/homecompute-model-update-monitor/check-model-updates.py' \
        "''${arguments[@]}"
    '';
  };

  notifyAttention = pkgs.writeShellApplication {
    name = "homecompute-model-update-notify";
    runtimeInputs = [ pkgs.coreutils pkgs.curl pkgs.jq pkgs.systemd ];
    text = ''
      report='${reportFile}'
      digest_file="$(dirname "$report")/last-notified.sha256"
      [ -r "$report" ] || exit 0
      jq -e '.status == "attention"' "$report" >/dev/null || exit 0
      [ "''${HOMECOMPUTE_UPDATE_NOTIFICATIONS:-true}" = true ] || exit 0
      digest="$(sha256sum "$report" | cut -d ' ' -f 1)"
      if [ -r "$digest_file" ] && [ "$(cat "$digest_file")" = "$digest" ]; then exit 0; fi
      summary="$(jq -c '.summary' "$report")"
      if [ -n "''${HOMECOMPUTE_UPDATE_NOTIFICATION_URL:-}" ]; then
        case "$HOMECOMPUTE_UPDATE_NOTIFICATION_URL" in https://*) ;; *) echo 'notification URL must use HTTPS' >&2; exit 1 ;; esac
        curl --fail --silent --show-error --proto '=https' --max-time 30 \
          -H 'Content-Type: application/json' \
          --data-binary "@$report" "$HOMECOMPUTE_UPDATE_NOTIFICATION_URL" >/dev/null
      else
        systemd-cat -t homecompute-model-update -p warning \
          echo "Model/runtime update review requires attention: $summary (configure ${notificationEnvironment} for webhook delivery)"
      fi
      temporary="$(mktemp "$(dirname "$digest_file")/.last-notified.XXXXXX")"
      printf '%s\n' "$digest" > "$temporary"
      chmod 0600 "$temporary"
      mv -f "$temporary" "$digest_file"
    '';
  };
in
{
  systemd.services.homecompute-model-update-monitor = {
    description = "Check reviewed model and runtime metadata for updates";
    after = [ "network-online.target" ];
    wants = [ "network-online.target" ];
    unitConfig.OnSuccess = "homecompute-model-update-notify.service";
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${runMonitor}/bin/homecompute-model-update-monitor";
      TimeoutStartSec = "15min";
      DynamicUser = true;
      StateDirectory = "homecompute-model-update-check";
      StateDirectoryMode = "0700";
      UMask = "0077";

      AmbientCapabilities = "";
      CapabilityBoundingSet = "";
      DevicePolicy = "closed";
      LockPersonality = true;
      MemoryDenyWriteExecute = true;
      NoNewPrivileges = true;
      PrivateDevices = true;
      PrivateTmp = true;
      ProtectClock = true;
      ProtectControlGroups = true;
      ProtectHome = true;
      ProtectHostname = true;
      ProtectKernelLogs = true;
      ProtectKernelModules = true;
      ProtectKernelTunables = true;
      ProtectProc = "invisible";
      ProtectSystem = "strict";
      ReadOnlyPaths = [
        "-${pinsFile}"
        "-${selectionFile}"
      ];
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

  systemd.services.homecompute-model-update-notify = {
    description = "Notify once when the HomeCompute update report needs attention";
    after = [ "homecompute-model-update-monitor.service" ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${notifyAttention}/bin/homecompute-model-update-notify";
      EnvironmentFile = "-${notificationEnvironment}";
      UMask = "0077";
      NoNewPrivileges = true;
      PrivateDevices = true;
      PrivateTmp = true;
      ProtectHome = true;
      ProtectSystem = "strict";
      ReadWritePaths = [ "/var/lib/homecompute/model-update-check" ];
    };
  };

  systemd.timers.homecompute-model-update-monitor = {
    description = "Run the weekly HomeCompute model update review";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "Mon *-*-* 09:07:00 Europe/Copenhagen";
      Persistent = true;
      AccuracySec = "1min";
      Unit = "homecompute-model-update-monitor.service";
    };
  };
}
