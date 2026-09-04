{ lib, ... }:
{
  # These files contain only image references, addresses, and secret paths.
  # SOPS supplies the actual credentials at activation time.
  environment.etc."homecompute/control-plane.env" = {
    mode = "0600";
    text = lib.replaceStrings
      [ "CONTROL_PLANE_BIND_ADDRESS=127.0.0.1" "AI_FQDN=ai.home.arpa" ]
      [ "CONTROL_PLANE_BIND_ADDRESS=100.110.248.102" "AI_FQDN=home-core.tail479ad.ts.net" ]
      (builtins.readFile ../../config/control-plane.env.example);
  };
  environment.etc."homecompute/automation.env" = {
    mode = "0600";
    source = ../../config/automation.env.example;
  };
  environment.etc."homecompute/books_importer/runtime.env" = {
    mode = "0600";
    source = ../../config/books_importer.env.example;
  };
}
