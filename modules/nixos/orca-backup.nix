{ pkgs, ... }:

{
  systemd.tmpfiles.rules = [
    "d /srv/state/orca 0700 12074 12074 - -"
    "d /srv/state/orca/projects 0700 12074 12074 - -"
    "d /var/lib/homecompute/orca 0700 root root - -"
  ];

  systemd.services.homecompute-orca-backup = {
    description = "Cold backup of Orca sessions and development worktrees";
    after = [ "docker.service" ];
    requires = [ "docker.service" ];
    unitConfig = {
      ConditionPathIsDirectory = "/srv/state/orca/profile";
      RequiresMountsFor = [ "/srv/state/orca" "/srv/backups" ];
    };
    path = with pkgs; [ bash coreutils docker findutils gnutar gzip util-linux ];
    serviceConfig = {
      Type = "oneshot";
      ExecStart = "${pkgs.bash}/bin/bash ${../../scripts/backup-orca.sh}";
      SuccessExitStatus = "75";
      TimeoutStartSec = "20min";
      UMask = "0077";
    };
  };

  systemd.timers.homecompute-orca-backup = {
    description = "Back up Orca before the NAS archive window";
    wantedBy = [ "timers.target" ];
    timerConfig = {
      OnCalendar = "*-*-* 02:30:00 Europe/Copenhagen";
      Persistent = true;
    };
  };
}
