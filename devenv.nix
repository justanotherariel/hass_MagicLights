# Based on ~/Work/open-home-foundation/devenv.nix, trimmed down to what a
# single HA custom integration needs.
{ pkgs, lib, config, ... }:

{
  cachix.enable = false;

  packages = with pkgs; [
    git
    jq

    # ruff from nixpkgs instead of the venv: the pip wheel ships a
    # foreign-glibc binary that only runs under nix-ld.
    ruff

    # HA runtime dependencies
    bluez
    ffmpeg
    libyaml
    libxml2
    libpcap
    libjpeg # libjpeg-turbo in nixpkgs
    zlib
    openssl
  ];

  languages.python = {
    enable = true;
    # HA 2026.3+ requires Python >= 3.14.2
    version = "3.14.5";
    venv.enable = true;
    venv.requirements = ./requirements.txt;
    # Shared libraries that C-extension wheels dlopen at runtime.
    libraries = with pkgs; [
      libyaml
      libxml2
      libjpeg
      libpcap
      openssl
      zlib
    ];
  };

  env = {
    # TLS trust roots — pure-Nix shells don't inherit them by default.
    SSL_CERT_FILE = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";
    NIX_SSL_CERT_FILE = "${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt";
  };

  scripts = {
    develop.exec = ''exec "$DEVENV_ROOT"/scripts/develop "$@"'';
    lint.exec = ''exec "$DEVENV_ROOT"/scripts/lint "$@"'';
  };

  enterShell = ''
    echo "magic-lights dev shell — run 'develop' to start HA (port 9123), 'lint' to lint"
  '';
}
