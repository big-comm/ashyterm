{
  pkgs,
  lib,
  callPackage,
  python3Packages,
  vte-gtk4,
  gtk4,
  libadwaita,
  rsync,
  sshpass,
  pkg-config,
  libsecret,
  wrapGAppsHook4,
  gobject-introspection,
  makeWrapper,
  withJemalloc ? true,
  withMimalloc ? false,
  withTcmalloc ? false,
  withPerformance ? false,
  withSyntaxHighlighting ? false,
  withBackup ? false,
}:

let
  libchildenv = callPackage ./nix/libchildenv.nix { };

  allocatorCfg =
    if withJemalloc then {
      package = pkgs.jemalloc;
      soName = "libjemalloc.so";
      envArgs = "--set MALLOC_CONF narenas:1";
      childEnvRules = "LD_PRELOAD,MALLOC_CONF,CHILD_ENV_RULES";
    }
    else if withMimalloc then {
      package = pkgs.mimalloc;
      soName = "libmimalloc.so";
      envArgs = "--set MIMALLOC_PURGE_DELAY 0";
      childEnvRules = "LD_PRELOAD,MIMALLOC_PURGE_DELAY,CHILD_ENV_RULES";
    }
    else if withTcmalloc then {
      package = pkgs.gperftools;
      soName = "libtcmalloc.so";
      envArgs = "--set TCMALLOC_AGGRESSIVE_DECOMMIT 1";
      childEnvRules = "LD_PRELOAD,TCMALLOC_AGGRESSIVE_DECOMMIT,CHILD_ENV_RULES";
    }
    else null;

  useAllocator = allocatorCfg != null;
in
python3Packages.buildPythonApplication {
  pname = "ashyterm";
  version = "0.1.0";

  src = ./.;

  pyproject = true;

  build-system = with python3Packages; [ uv-build ];
  dependencies = with python3Packages;
    [
      pygobject3
      pycairo
      setproctitle
      requests
      psutil
    ]
    ++ lib.optionals withPerformance [ regex ]
    ++ lib.optionals withSyntaxHighlighting [ pygments ]
    ++ lib.optionals withBackup [ py7zr ];

  nativeBuildInputs = [
    pkg-config
    wrapGAppsHook4
    gobject-introspection
  ];
  buildInputs = [
    vte-gtk4
    gtk4
    libadwaita
    rsync
    sshpass
    libsecret
  ] ++ lib.optionals useAllocator [
    allocatorCfg.package
    libchildenv
  ];

  # Prevent both wrapGAppsHook4 and buildPythonApplication from creating
  # compiled C binary wrappers.
  # A single shell-script wrapper avoids this because the shell process
  # itself was started before LD_PRELOAD was set, so libchildenv is not
  # active in it.
  dontWrapGApps = true;
  dontWrapPythonPrograms = true;

  postInstall = ''
    cp $src/usr/share $out/share -r
  '';

  postFixup = ''
    source ${makeWrapper}/nix-support/setup-hook
    wrapProgram $out/bin/ashyterm \
      "''${gappsWrapperArgs[@]}" \
      --prefix PATH : "${python3Packages.python}/bin" \
      --prefix PYTHONPATH : "$PYTHONPATH" \
  '' + lib.optionalString useAllocator ''
      --set LD_PRELOAD "${libchildenv}/lib/libchildenv.so:${allocatorCfg.package}/lib/${allocatorCfg.soName}" \
      ${allocatorCfg.envArgs} \
      --set CHILD_ENV_RULES "${allocatorCfg.childEnvRules}"
  '';
}
