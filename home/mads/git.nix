{ ... }:
{
  programs.git = {
    enable = true;
    settings = {
      init.defaultBranch = "main";
      fetch.prune = true;
      pull.ff = "only";
      push.autoSetupRemote = true;
      rerere.enabled = true;
    };
  };
}
