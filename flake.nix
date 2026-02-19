{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixpkgs-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs =
    {
      self,
      nixpkgs,
      flake-utils,
    }:
    flake-utils.lib.eachDefaultSystem (
      system:
      let
        pkgs = import nixpkgs { inherit system; };
        # Default allocator is jemalloc. To build with a different allocator:
        #   nix build --expr \
        #    '(builtins.getFlake (toString ./.)).packages.x86_64-linux.ashyterm.override { withJemalloc = false }'
        # Or from another flake:
        #   ashyterm.packages.${system}.ashyterm.override { withJemalloc = false; withMimalloc = true; }
        # Available flags: withJemalloc (default: true), withMimalloc, withTcmalloc
      in
      {
        packages = {
          default = self.packages.${system}.ashyterm;
          ashyterm = pkgs.callPackage ./. { inherit pkgs; };
          ashyterm-performance = pkgs.callPackage ./. { inherit pkgs; withPerformance = true; };
          ashyterm-highlighting = pkgs.callPackage ./. { inherit pkgs; withSyntaxHighlighting = true; };
          ashyterm-backup = pkgs.callPackage ./. { inherit pkgs; withBackup = true; };
          ashyterm-all = pkgs.callPackage ./. {
            inherit pkgs;
            withPerformance = true;
            withSyntaxHighlighting = true;
            withBackup = true;
          };
        };
        devShells.default = pkgs.mkShell {
          inputsFrom = [ self.packages.${system}.default ];
          packages = with pkgs; [ uv ];
          shellHook = ''
            export PYTHONPATH="$PWD/src:$PYTHONPATH"
          '';
        };
      }
    );

}
