{
  config,
  lib,
  ...
}:
let
  cfg = config.homecompute.secrets;
in
{
  options.homecompute.secrets = {
    enable = lib.mkEnableOption "sops-nix secrets for ai-services-01";

    defaultSopsFile = lib.mkOption {
      type = lib.types.nullOr lib.types.path;
      default = null;
      description = "Encrypted SOPS file for this host.";
    };

    ageKeyFile = lib.mkOption {
      type = lib.types.str;
      default = "/var/lib/sops-nix/key.txt";
      description = "Persistent age identity installed outside the Nix store.";
    };
  };

  config = lib.mkIf cfg.enable (
    lib.mkMerge [
      {
        assertions = [
          {
            assertion = cfg.defaultSopsFile != null;
            message = "homecompute.secrets.defaultSopsFile must reference an encrypted file";
          }
        ];
      }

      (lib.mkIf (cfg.defaultSopsFile != null) {
        sops = {
          defaultSopsFile = cfg.defaultSopsFile;
          validateSopsFiles = true;
          age = {
            keyFile = cfg.ageKeyFile;
            generateKey = false;
          };

          secrets = {
            "control-plane/compute-api-key" = {
              group = "homecompute-secrets";
              mode = "0440";
            };
            "control-plane/litellm-master-key" = {
              group = "homecompute-secrets";
              mode = "0440";
            };
            "control-plane/litellm-salt-key" = {
              group = "homecompute-secrets";
              mode = "0440";
            };
            "control-plane/postgres-admin-password" = {
              group = "homecompute-secrets";
              mode = "0440";
            };
            "control-plane/postgres-app-password" = {
              group = "homecompute-secrets";
              mode = "0440";
            };
            "restic/password" = {
              mode = "0400";
            };
          };
        };
      })
    ]
  );
}
