{ lib, ... }:
{
  # These files contain only image references, addresses, and secret paths.
  # SOPS supplies the actual credentials at activation time.
  environment.etc."homecompute/control-plane.env" = {
    mode = "0600";
    text = lib.replaceStrings
      [
        "CONTROL_PLANE_TAILSCALE_BIND_ADDRESS=127.0.0.1"
        "CONTROL_PLANE_LAN_BIND_ADDRESS=127.0.0.2"
        "AI_LEGACY_FQDN=home-core.invalid"
      ]
      [
        "CONTROL_PLANE_TAILSCALE_BIND_ADDRESS=100.110.248.102"
        "CONTROL_PLANE_LAN_BIND_ADDRESS=192.168.30.122"
        "AI_LEGACY_FQDN=home-core.tail479ad.ts.net"
      ]
      (builtins.readFile ../../config/control-plane.env.example);
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
}
