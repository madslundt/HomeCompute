{ pkgs, ... }:
{
  boot.loader.systemd-boot.enable = true;
  boot.loader.efi.canTouchEfiVariables = true;

  time.timeZone = "Europe/Copenhagen";
  i18n.defaultLocale = "en_DK.UTF-8";

  nix = {
    settings = {
      experimental-features = [
        "nix-command"
        "flakes"
      ];
      auto-optimise-store = true;
      allowed-users = [
        "root"
        "@wheel"
      ];
    };
    gc = {
      automatic = true;
      dates = "weekly";
      options = "--delete-older-than 30d";
    };
  };

  users.users.mads = {
    isNormalUser = true;
    description = "Mads";
    shell = pkgs.bashInteractive;
    extraGroups = [ "wheel" ];
  };

  # This account is reached with a reviewed SSH key and has no reusable login
  # password. Requiring a password here would make administrative access
  # unusable after installation.
  security.sudo.wheelNeedsPassword = false;

  environment.systemPackages = with pkgs; [
    age
    curl
    docker-compose
    git
    jq
    restic
    smartmontools
    sops
    vim
  ];

  services.fstrim.enable = true;
  services.smartd.enable = true;

  system.stateVersion = "26.05";
}
