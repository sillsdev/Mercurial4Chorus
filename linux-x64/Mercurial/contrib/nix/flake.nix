# flake.nix - Nix-defined package and devel env for the Mercurial project.
#
# Copyright 2021-2023 Pacien TRAN-GIRARD <pacien.trangirard@pacien.net>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

# Usage summary, from the root of this repository:
#
# Enter a shell with development tools:
#   nix develop 'hg+file:.?dir=contrib/nix'
#
# Running mercurial:
#   nix run 'hg+file:.?dir=contrib/nix' -- version
#
# Running the test suite in a sandbox:
#   nix build 'hg+file:.?dir=contrib/nix#mercurial-tests' -L

{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-22.11";
    nixpkgs-black.url = "github:NixOS/nixpkgs/c7cb72b0";  # black 20.8b1
    # rust-overlay.url = "github:oxalica/rust-overlay";
    flake-utils.url = "github:numtide/flake-utils";
    flaky-utils.url = "git+https://cgit.pacien.net/libs/flaky-utils";
  };

  outputs = {
    self
  , nixpkgs
  , nixpkgs-black
  # , rust-overlay
  , flake-utils
  , flaky-utils
  }:
  flake-utils.lib.eachDefaultSystem (system:
  let
    # overlays = [ (import rust-overlay) ];
    pkgs = import nixpkgs { inherit system; };

    # We're in the contrib/nix sub-directory.
    src = ../..;

    # For snapshots, to satisfy extension minimum version requirements.
    dummyVersion = "99.99";

    pin = {
      # The test suite has issues with the latest/current versions of Python.
      # Use an older recommended version instead, matching the CI.
      python = pkgs.python39;

      # The project uses a pinned version (rust/clippy.toml) for compiling,
      # but uses formatter features from nightly.
      # TODO: make cargo use the formatter from nightly automatically
      #       (not supported by rustup/cargo yet? workaround?)
      # rustPlatform = pkgs.rust-bin.stable."1.79.0".default;
      # rustPlatformFormatter = pkgs.rust-bin.nightly."2023-04-20".default;

      # The CI uses an old version of the Black code formatter,
      # itself depending on old Python libraries.
      # The formatting rules have changed in more recent versions.
      inherit (import nixpkgs-black { inherit system; }) black;
    };

  in rec {
    apps.mercurial = apps.mercurial-rust;
    apps.default = apps.mercurial;
    apps.mercurial-c = flake-utils.lib.mkApp {
      drv = packages.mercurial-c;
    };
    apps.mercurial-rust = flake-utils.lib.mkApp {
      drv = packages.mercurial-rust;
    };

    packages.mercurial = packages.mercurial-rust;
    packages.default = packages.mercurial;

    packages.mercurial-c = pin.python.pkgs.buildPythonApplication {
      format = "other";
      pname = "mercurial";
      version = "SNAPSHOT";
      passthru.exePath = "/bin/hg";
      inherit src;

      postPatch = ''
        echo 'version = b"${toString dummyVersion}"' \
          > mercurial/__version__.py

        patchShebangs .

        for f in **/*.{py,c,t}; do
          # not only used in shebangs
          substituteAllInPlace "$f" '/bin/sh' '${pkgs.stdenv.shell}'
        done
      '';

      buildInputs = with pin.python.pkgs; [
        docutils
      ];

      nativeBuildInputs = with pkgs; [
        gettext
        installShellFiles
      ];

      makeFlags = [
        "PREFIX=$(out)"
      ];

      buildPhase = ''
        make local
      '';

      # Test suite is huge ; run on-demand in a separate package instead.
      doCheck = false;
    };

    packages.mercurial-rust = packages.mercurial-c.overrideAttrs (super: {
      cargoRoot = "rust";
      cargoDeps = pkgs.rustPlatform.importCargoLock {
        lockFile = "${src}/rust/Cargo.lock";
      };

      nativeBuildInputs = (super.nativeBuildInputs or []) ++ (
        with pkgs.rustPlatform; [
          cargoSetupHook
          rust.cargo
          rust.rustc
        ]
      );

      makeFlags = (super.makeFlags or []) ++ [
        "PURE=--rust"
      ];
    });

    packages.mercurial-tests = pkgs.stdenv.mkDerivation {
      pname = "mercurial-tests";
      version = "SNAPSHOT";
      inherit src;

      buildInputs = with pkgs; [
        pin.python
        pin.black
        unzip
        which
        sqlite
      ];

      postPatch = (packages.mercurial.postPatch or "") + ''
        # * paths emitted by our wrapped hg look like ..hg-wrapped-wrapped
        # * 'hg' is a wrapper; don't run using python directly
        for f in **/*.t; do
          substituteInPlace 2>/dev/null "$f" \
            --replace '*/hg:' '*/*hg*:' \
            --replace '"$PYTHON" "$BINDIR"/hg' '"$BINDIR"/hg'
        done
      '';

      buildPhase = ''
        export HGTEST_REAL_HG="${packages.mercurial}/bin/hg"
        export HGMODULEPOLICY="rust+c"
        export HGTESTFLAGS="--blacklist blacklists/nix"
        make check 2>&1 | tee "$out"
      '';
    };

    devShell = flaky-utils.lib.mkDevShell {
      inherit pkgs;

      tools = [
        pin.python
        pin.black
      ];
    };
  });
}
