{
  description = "HomeCompute NixOS infrastructure";

  # Match the pinned OMP flake's signed community cache. Input-level nixConfig
  # is not inherited by the root flake, so declare it here to reuse cached OMP
  # dependencies during checks and deployments.
  nixConfig = {
    extra-substituters = [ "https://nix-community.cachix.org" ];
    extra-trusted-public-keys = [
      "nix-community.cachix.org-1:mB9FSh9qf2dCimDSUo8Zy7bkq5CX+/rkCWyvRCYg3Fs="
    ];
  };

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

    home-manager = {
      url = "github:nix-community/home-manager/release-26.05";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    sops-nix = {
      url = "github:Mic92/sops-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    omp.url = "github:can1357/oh-my-pi";
  };

  outputs =
    {
      nixpkgs,
      home-manager,
      omp,
      sops-nix,
      ...
    }:
    {
      nixosConfigurations.home-core = nixpkgs.lib.nixosSystem {
        system = "x86_64-linux";
        modules = [
          ./hosts/home-core
          sops-nix.nixosModules.sops
          home-manager.nixosModules.home-manager
          {
            home-manager = {
              useGlobalPkgs = true;
              useUserPackages = true;
              users.mads = ./home/mads;
              users.agent.imports = [
                omp.homeManagerModules.default
                ./home/agent
              ];
            };
          }
        ];
      };

      formatter.x86_64-linux = nixpkgs.legacyPackages.x86_64-linux.nixfmt;
      formatter.aarch64-darwin = nixpkgs.legacyPackages.aarch64-darwin.nixfmt;
    };
}
