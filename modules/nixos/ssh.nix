{ ... }:
{
  services.openssh = {
    enable = true;
    openFirewall = false;
    settings = {
      AllowUsers = [ "mads" ];
      KbdInteractiveAuthentication = false;
      PasswordAuthentication = false;
      PermitRootLogin = "no";
      X11Forwarding = false;
    };
  };

  # Add mads.openssh.authorizedKeys in the host module before remote install.
  # Public keys are configuration, not secrets; private keys never enter Git.
}
