{ pkgs, ... }:
let
  # Nixpkgs 26.05 currently carries Pi 0.75.4, which predates the 0.79.0
  # project-trust security fix. Package the verified upstream release instead.
  piCodingAgent = pkgs.stdenvNoCC.mkDerivation {
    pname = "pi-coding-agent";
    version = "0.85.1";

    src = pkgs.fetchurl {
      url = "https://github.com/earendil-works/pi/releases/download/v0.85.1/pi-linux-x64.tar.gz";
      hash = "sha256-SU5Jj0fXTSH0CzOG9qXpIaPUlTGhacq1W72soOof4lo=";
    };

    darkTheme = pkgs.fetchurl {
      url = "https://raw.githubusercontent.com/earendil-works/pi/v0.85.1/packages/coding-agent/src/modes/interactive/theme/dark.json";
      hash = "sha256-EDpa7LdKLatcyQPJdBhF7mFYZYzi/25URZSHhBFurvg=";
    };

    lightTheme = pkgs.fetchurl {
      url = "https://raw.githubusercontent.com/earendil-works/pi/v0.85.1/packages/coding-agent/src/modes/interactive/theme/light.json";
      hash = "sha256-FMcXK6fnXqtvUJUE3oBq8PLZ/4F7+0XZUzv/Fcfm5lc=";
    };

    nativeBuildInputs = [ pkgs.autoPatchelfHook ];
    dontBuild = true;
    # Pi is a Bun single-file executable with its application payload appended
    # to the ELF. Stripping turns it back into a plain Bun runtime.
    dontStrip = true;

    installPhase = ''
      runHook preInstall
      install -Dm755 pi "$out/bin/pi"
      install -Dm644 "$darkTheme" "$out/bin/theme/dark.json"
      install -Dm644 "$lightTheme" "$out/bin/theme/light.json"
      mkdir -p "$out/share/pi"
      cp -R examples "$out/share/pi/"
      runHook postInstall
    '';
  };

  agentSession = pkgs.writeShellApplication {
    name = "agent-session";
    runtimeInputs = [
      pkgs.coreutils
      pkgs.findutils
      pkgs.tmux
    ];
    text = ''
      usage() {
        echo "Usage: agent-session [pi|omp] [repository] [session]"
        echo "       agent-session --list"
      }

      if [[ "''${1:-}" == "--help" ]]; then
        usage
        exit 0
      fi

      if [[ "''${1:-}" == "--list" ]]; then
        echo "Repositories in $HOME/src:"
        find "$HOME/src" -mindepth 1 -maxdepth 1 -type d -printf '  %f\n' | sort
        echo
        echo "tmux sessions:"
        tmux list-sessions 2>/dev/null || echo "  none"
        exit 0
      fi

      harness="''${1:-}"
      if [[ -z "$harness" ]]; then
        echo "Choose a harness:"
        echo "  1) Pi (default)"
        echo "  2) OMP"
        read -r -p "> " choice
        case "$choice" in
          "" | 1 | pi | Pi) harness="pi" ;;
          2 | omp | OMP) harness="omp" ;;
          *) echo "Invalid choice: $choice" >&2; exit 2 ;;
        esac
      fi

      case "$harness" in
        pi | omp) ;;
        *) usage >&2; exit 2 ;;
      esac

      repository="''${2:-HomeCompute}"
      if [[ ! "$repository" =~ ^[A-Za-z0-9._-]+$ ]]; then
        echo "Repository must be a directory name below ~/src" >&2
        exit 2
      fi

      repository_path="$HOME/src/$repository"
      if [[ ! -d "$repository_path/.git" && ! -f "$repository_path/.git" ]]; then
        echo "Not a Git repository: $repository_path" >&2
        exit 2
      fi

      session="''${3:-$harness-$repository}"
      if [[ ! "$session" =~ ^[A-Za-z0-9._-]+$ ]]; then
        echo "Session may contain only letters, numbers, dots, underscores, and hyphens" >&2
        exit 2
      fi

      exec tmux new-session -A -s "$session" -c "$repository_path" "$harness"
    '';
  };
in
{
  home = {
    stateVersion = "26.05";
    packages = with pkgs; [
      bat
      agentSession
      eza
      fd
      fzf
      git
      jq
      piCodingAgent
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

  programs = {
    bash = {
      enable = true;
      enableCompletion = true;
      historyControl = [
        "erasedups"
        "ignoredups"
        "ignorespace"
      ];
      historyFileSize = 100000;
      historySize = 10000;
    };

    git = {
      enable = true;
      settings = {
        init.defaultBranch = "main";
        fetch.prune = true;
        pull.ff = "only";
        push.autoSetupRemote = true;
        rerere.enabled = true;
      };
    };

    omp = {
      enable = true;
      settings = {
        async.enabled = true;
        startup.quiet = true;
        task = {
          maxConcurrency = 2;
          isolation.enabled = true;
        };
        tools.approvalMode = "write";
      };
    };

    tmux = {
      enable = true;
      baseIndex = 1;
      clock24 = true;
      escapeTime = 0;
      extraConfig = ''
        set -g extended-keys on
        set -g extended-keys-format csi-u
      '';
      historyLimit = 100000;
      mouse = true;
      terminal = "screen-256color";
    };
  };

  xdg.enable = true;

  home.activation.agentWorkspace = {
    after = [ "writeBoundary" ];
    before = [ ];
    data = ''
      run mkdir -p "$HOME/src" "$HOME/worktrees"
      run chmod 700 "$HOME" "$HOME/.omp" 2>/dev/null || true
    '';
  };
}
