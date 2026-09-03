{ lib, ... }:
{
  # DHCP keeps the first boot hardware-independent. Pin addresses in this
  # module after recording the target interface names and network contract.
  networking.useDHCP = lib.mkDefault true;
  networking.networkmanager.enable = false;

  services.resolved.enable = true;
}
