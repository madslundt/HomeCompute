{ ... }:
{
  programs.bash = {
    enable = true;
    enableCompletion = true;
    historyControl = [
      "erasedups"
      "ignoredups"
      "ignorespace"
    ];
    historyFileSize = 100000;
    historySize = 10000;

    shellAliases = {
      cat = "bat --plain";
      la = "eza --all --group-directories-first";
      ll = "eza --long --all --group-directories-first --git";
      tree = "eza --tree";
      gs = "git status --short --branch";
    };
  };

  programs.fzf.enable = true;
}
