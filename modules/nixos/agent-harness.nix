{ pkgs, ... }:
let
  agentUid = 1001;
in
{
  users.users.agent = {
    isNormalUser = true;
    uid = agentUid;
    description = "Unprivileged coding agent";
    shell = pkgs.bashInteractive;
    home = "/home/agent";
    homeMode = "0700";
    createHome = true;
    linger = true;

    # Reuse the reviewed workstation key for initial access. The account is
    # intentionally separate from `mads`: it has no wheel or Docker membership.
    openssh.authorizedKeys.keys = [
      "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAILT+ES2e5sbGFzBMLOWKZMawBm/kyadBthAldjAmK8Uc agent@home-core"
    ];
  };

  # Bound every process owned by the account, including tmux servers, harnesses,
  # compiler processes, and subagents. Leave at least half of the host's
  # measured 48 GiB RAM and most CPU capacity available to production services.
  systemd.slices."user-${toString agentUid}".sliceConfig = {
    CPUQuota = "400%";
    MemoryHigh = "16G";
    MemoryMax = "24G";
    TasksMax = 4096;
  };
}
