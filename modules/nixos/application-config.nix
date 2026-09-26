{ config, lib, ... }:
let
  directControlPlaneEnvironment = builtins.readFile ../../config/control-plane.env.example;
  controlPlaneEnvironment =
    if config.homecompute.computeSshTunnel.enable then
      lib.replaceStrings
        [
          "COMPUTE_TRANSPORT=dedicated-link"
          "COMPUTE_AUTOMATION_BASE_URL=http://10.77.10.10:8005/v1"
          "COMPUTE_HOME_BASE_URL=http://10.77.10.10:8006/v1"
          "PLAPRE_WYOMING_UPSTREAM_HOST=10.77.10.10"
          "PLAPRE_WYOMING_UPSTREAM_PORT=10201"
          "HVISKE_WYOMING_UPSTREAM_HOST=10.77.10.10"
          "HVISKE_WYOMING_UPSTREAM_PORT=10301"
        ]
        [
          "COMPUTE_TRANSPORT=ssh-loopback-fallback"
          "COMPUTE_AUTOMATION_BASE_URL=http://172.28.200.1:18005/v1"
          "COMPUTE_HOME_BASE_URL=http://172.28.200.1:18006/v1"
          "PLAPRE_WYOMING_UPSTREAM_HOST=172.28.200.1"
          "PLAPRE_WYOMING_UPSTREAM_PORT=18201"
          "HVISKE_WYOMING_UPSTREAM_HOST=172.28.200.1"
          "HVISKE_WYOMING_UPSTREAM_PORT=18301"
        ]
        directControlPlaneEnvironment
    else
      directControlPlaneEnvironment;
in
{
  # These files contain only image references, addresses, and secret paths.
  # SOPS supplies the actual credentials at activation time.
  environment.etc."homecompute/control-plane.env" = {
    mode = "0600";
    text = lib.replaceStrings
      [
        "CONTROL_PLANE_TAILSCALE_BIND_ADDRESS=127.0.0.1"
        "CONTROL_PLANE_TAILSCALE_HTTPS_PORT=443"
        "CONTROL_PLANE_LAN_BIND_ADDRESS=127.0.0.2"
        "AI_LEGACY_FQDN=home-core.invalid"
      ]
      [
        "CONTROL_PLANE_TAILSCALE_BIND_ADDRESS=100.110.248.102"
        "CONTROL_PLANE_TAILSCALE_HTTPS_PORT=8443"
        "CONTROL_PLANE_LAN_BIND_ADDRESS=192.168.30.122"
        "AI_LEGACY_FQDN=home-core.tail479ad.ts.net"
      ]
      controlPlaneEnvironment;
  };
  environment.etc."homecompute/automation.env" = {
    mode = "0600";
    source = ../../config/automation.env.example;
  };
  environment.etc."homecompute/homepage.env" = {
    mode = "0600";
    source = ../../config/homepage.env.example;
  };
  environment.etc."homecompute/books_importer/runtime.env" = {
    mode = "0600";
    source = ../../config/books_importer.env.example;
  };
  environment.etc."homecompute/piper-tts.env" = {
    mode = "0600";
    source = ../../config/piper-tts.env.example;
  };
  environment.etc."homecompute/wyoming-stt.env" = {
    mode = "0600";
    source = ../../config/wyoming-stt.env.example;
  };
}
