{ pkgs, ... }:
{
  imports = [
    ./shell.nix
    ./git.nix
    ./tmux.nix
  ];

  home = {
    stateVersion = "26.05";

    packages = with pkgs; [
      bat
      eza
      fd
      htop
      ripgrep
      tree
    ];

    sessionVariables = {
      EDITOR = "vim";
      VISUAL = "vim";
      PAGER = "less";
      LESS = "-FRX";
    };
  };

  xdg.enable = true;
}
