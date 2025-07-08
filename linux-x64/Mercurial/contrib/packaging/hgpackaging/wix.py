# wix.py - WiX installer functionality
#
# Copyright 2019 Gregory Szorc <gregory.szorc@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

# no-check-code because Python 3 native.

import json
import os
import pathlib
import shutil
import typing

from .pyoxidizer import (
    build_docs_html,
    run_pyoxidizer,
)


def build_installer_pyoxidizer(
    source_dir: pathlib.Path,
    target_triple: str,
    msi_name='mercurial',
    version=None,
    extra_wxs: typing.Optional[typing.Dict[str, str]] = None,
    extra_features: typing.Optional[typing.List[str]] = None,
    signing_info: typing.Optional[typing.Dict[str, str]] = None,
    extra_pyoxidizer_vars=None,
):
    """Build a WiX MSI installer using PyOxidizer."""
    hg_build_dir = source_dir / "build"
    build_dir = hg_build_dir / ("wix-%s" % target_triple)

    build_dir.mkdir(parents=True, exist_ok=True)

    # Need to ensure docs HTML is built because this isn't done as part of
    # `pip install Mercurial`.
    build_docs_html(source_dir)

    build_vars = {}

    if msi_name:
        build_vars["MSI_NAME"] = msi_name

    if version:
        build_vars["VERSION"] = version

    if extra_features:
        build_vars["EXTRA_MSI_FEATURES"] = ";".join(extra_features)

    if signing_info:
        if signing_info["cert_path"]:
            build_vars["SIGNING_PFX_PATH"] = signing_info["cert_path"]
        if signing_info["cert_password"]:
            build_vars["SIGNING_PFX_PASSWORD"] = signing_info["cert_password"]
        if signing_info["subject_name"]:
            build_vars["SIGNING_SUBJECT_NAME"] = signing_info["subject_name"]
        if signing_info["timestamp_url"]:
            build_vars["TIME_STAMP_SERVER_URL"] = signing_info["timestamp_url"]

    if extra_pyoxidizer_vars:
        build_vars.update(json.loads(extra_pyoxidizer_vars))

    if extra_wxs:
        raise Exception(
            "support for extra .wxs files has been temporarily dropped"
        )

    out_dir = run_pyoxidizer(
        source_dir,
        build_dir,
        target_triple,
        build_vars=build_vars,
        target="msi",
    )

    msi_dir = out_dir / "msi"
    msi_files = [f for f in os.listdir(msi_dir) if f.endswith(".msi")]

    if len(msi_files) != 1:
        raise Exception("expected exactly 1 .msi file; got %d" % len(msi_files))

    msi_filename = msi_files[0]

    msi_path = msi_dir / msi_filename
    dist_path = source_dir / "dist" / msi_filename

    dist_path.parent.mkdir(parents=True, exist_ok=True)

    shutil.copyfile(msi_path, dist_path)

    return {
        "msi_path": dist_path,
    }
