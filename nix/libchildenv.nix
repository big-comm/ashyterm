{
  lib,
  stdenv,
  fetchFromGitHub,
}:

stdenv.mkDerivation {
  pname = "libchildenv";
  version = "unstable-2025-01-27";

  src = fetchFromGitHub {
    owner = "biglinux";
    repo = "libchildenv";
    rev = "d638f03320b0bb29696dc515489b70892cf3da9f";
    hash = "sha256-n9oKANktUUJyGpmzguqgx6oaACCx6tGLnSri4gs4b3Q=";
  };

  buildPhase = ''
    gcc -shared -fPIC -o libchildenv.so libchildenv.c -ldl
  '';

  installPhase = ''
    install -Dm755 libchildenv.so $out/lib/libchildenv.so
    install -Dm755 libchildenv.sh $out/bin/libchildenv
  '';

  meta = with lib; {
    description = "LD_PRELOAD library that strips environment variables from child processes";
    homepage = "https://github.com/biglinux/libchildenv";
    license = licenses.gpl3;
  };
}
