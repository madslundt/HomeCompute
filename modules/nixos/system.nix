{ pkgs, ... }:
{
  boot.loader.systemd-boot = {
    enable = true;

    # Each generation keeps a kernel and initrd on the ESP. Without a bound
    # they accumulate until the weekly collector prunes them at 30 days, which
    # can exhaust the ESP and fail the rebuild that would have fixed it.
    configurationLimit = 10;

    # The boot-entry editor lets anyone at the console append init=/bin/sh and
    # obtain an unauthenticated root shell, which would bypass the reviewed
    # SSH key that is otherwise the only administrative credential. Recovery
    # uses the generation menu and installer media instead.
    editor = false;
  };
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
