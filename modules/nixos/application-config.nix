{ lib, ... }:
{
  # These files contain only image references, addresses, and secret paths.
  # SOPS supplies the actual credentials at activation time.
  environment.etc."homecompute/control-plane.env" = {
    mode = "0600";
    text = lib.replaceStrings
      [
        "CONTROL_PLANE_LAN_BIND_ADDRESS=127.0.0.2"
      ]
      [
        "CONTROL_PLANE_LAN_BIND_ADDRESS=192.168.30.122"
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
  environment.etc."homecompute/piper-tts.env" = {
    mode = "0600";
    source = ../../config/piper-tts.env.example;
  };
  environment.etc."homecompute/wyoming-stt.env" = {
    mode = "0600";
    source = ../../config/wyoming-stt.env.example;
  };
}
