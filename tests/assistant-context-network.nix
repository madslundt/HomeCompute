{ flake }:
let
  lib = flake.inputs.nixpkgs.lib;
  base = flake.nixosConfigurations.home-core;
  edgeLine = builtins.head (builtins.filter (lib.hasPrefix "CADDY_EDGE_IP=")
    (lib.splitString "\n" (builtins.readFile (flake.outPath + "/config/control-plane.env.example"))));
  edgeAddress = lib.removePrefix "CADDY_EDGE_IP=" edgeLine;
  variant = inference: (base.extendModules {
    modules = [ {
      homecompute.assistantContext = {
        enable = lib.mkForce true;
        inference.embedding = lib.mkForce inference;
        inference.briefings = lib.mkForce false;
        delegation.enable = lib.mkForce false;
        delivery.enable = lib.mkForce false;
      };
    } ];
  }).config;
  disabled = (variant false).systemd.services.homecompute-assistant-context.serviceConfig;
  enabledConfig = variant true;
  enabled = enabledConfig.systemd.services.homecompute-assistant-context.serviceConfig;
in
assert disabled.IPAddressDeny == "any";
assert disabled.IPAddressAllow == [ "127.0.0.1/32" ];
assert enabled.IPAddressDeny == "any";
assert enabled.IPAddressAllow == [
  "127.0.0.1/32"
  "${enabledConfig.homecompute.assistantContext.inference.address}/32"
  "${edgeAddress}/32"
];
true
