# cli.py - Command line interface for automation
#
# Copyright 2019 Gregory Szorc <gregory.szorc@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

# no-check-code because Python 3 native.

import argparse
import os
import pathlib

from . import (
    inno,
    wix,
)

HERE = pathlib.Path(os.path.abspath(os.path.dirname(__file__)))
SOURCE_DIR = HERE.parent.parent.parent


def build_inno(pyoxidizer_target, iscc=None, version=None):
    if iscc:
        iscc = pathlib.Path(iscc)
    else:
        iscc = (
            pathlib.Path(os.environ["ProgramFiles(x86)"])
            / "Inno Setup 5"
            / "ISCC.exe"
        )

    build_dir = SOURCE_DIR / "build"

    inno.build_with_pyoxidizer(
        SOURCE_DIR, build_dir, pyoxidizer_target, iscc, version=version
    )


def build_wix(
    pyoxidizer_target,
    name=None,
    version=None,
    sign_sn=None,
    sign_cert=None,
    sign_password=None,
    sign_timestamp_url=None,
    extra_wxs=None,
    extra_features=None,
    extra_pyoxidizer_vars=None,
):
    kwargs = {
        "source_dir": SOURCE_DIR,
        "version": version,
        "target_triple": pyoxidizer_target,
        "extra_pyoxidizer_vars": extra_pyoxidizer_vars,
    }

    if extra_wxs:
        kwargs["extra_wxs"] = dict(
            thing.split("=") for thing in extra_wxs.split(",")
        )
    if extra_features:
        kwargs["extra_features"] = extra_features.split(",")

    if sign_sn or sign_cert:
        kwargs["signing_info"] = {
            "name": name,
            "subject_name": sign_sn,
            "cert_path": sign_cert,
            "cert_password": sign_password,
            "timestamp_url": sign_timestamp_url,
        }

    wix.build_installer_pyoxidizer(**kwargs)


def get_parser():
    parser = argparse.ArgumentParser()

    subparsers = parser.add_subparsers()

    sp = subparsers.add_parser("inno", help="Build Inno Setup installer")
    sp.add_argument(
        "--pyoxidizer-target",
        choices={"i686-pc-windows-msvc", "x86_64-pc-windows-msvc"},
        required=True,
        help="Build with PyOxidizer targeting this host triple",
    )
    sp.add_argument("--iscc", help="path to iscc.exe to use")
    sp.add_argument(
        "--version",
        help="Mercurial version string to use "
        "(detected from __version__.py if not defined)",
    )
    sp.set_defaults(func=build_inno)

    sp = subparsers.add_parser(
        "wix", help="Build Windows installer with WiX Toolset"
    )
    sp.add_argument("--name", help="Application name", default="Mercurial")
    sp.add_argument(
        "--pyoxidizer-target",
        choices={"i686-pc-windows-msvc", "x86_64-pc-windows-msvc"},
        required=True,
        help="Build with PyOxidizer targeting this host triple",
    )
    sp.add_argument(
        "--sign-sn",
        help="Subject name (or fragment thereof) of certificate "
        "to use for signing",
    )
    sp.add_argument(
        "--sign-cert", help="Path to certificate to use for signing"
    )
    sp.add_argument("--sign-password", help="Password for signing certificate")
    sp.add_argument(
        "--sign-timestamp-url",
        help="URL of timestamp server to use for signing",
    )
    sp.add_argument("--version", help="Version string to use")
    sp.add_argument(
        "--extra-wxs", help="CSV of path_to_wxs_file=working_dir_for_wxs_file"
    )
    sp.add_argument(
        "--extra-features",
        help=(
            "CSV of extra feature names to include "
            "in the installer from the extra wxs files"
        ),
    )

    sp.add_argument(
        "--extra-pyoxidizer-vars",
        help="json map of extra variables to pass to pyoxidizer",
    )

    sp.set_defaults(func=build_wix)

    return parser


def main():
    parser = get_parser()
    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        return

    kwargs = dict(vars(args))
    del kwargs["func"]

    args.func(**kwargs)
