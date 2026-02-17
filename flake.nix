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
      in
      {
        packages = {
          default = self.packages.${system}.ashyterm;
          ashyterm = pkgs.callPackage ./. { };
          ashyterm-performance = pkgs.callPackage ./. { withPerformance = true; };
          ashyterm-highlighting = pkgs.callPackage ./. { withSyntaxHighlighting = true; };
          ashyterm-backup = pkgs.callPackage ./. { withBackup = true; };
          ashyterm-all = pkgs.callPackage ./. {
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
