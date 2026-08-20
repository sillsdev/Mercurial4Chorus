# inno.py - Inno Setup functionality.
#
# Copyright 2019 Gregory Szorc <gregory.szorc@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

# no-check-code because Python 3 native.

import os
import pathlib
import shutil
import subprocess

import jinja2

from .pyoxidizer import create_pyoxidizer_install_layout
from .util import (
    normalize_windows_version,
    process_install_rules,
    read_version_py,
)


EXTRA_INSTALL_RULES = [
    ('contrib/win32/mercurial.ini', 'defaultrc/mercurial.rc'),
]

PACKAGE_FILES_METADATA = {
    'ReadMe.html': 'Flags: isreadme',
}


def build_with_pyoxidizer(
    source_dir: pathlib.Path,
    build_dir: pathlib.Path,
    target_triple: str,
    iscc_exe: pathlib.Path,
    version=None,
):
    """Build the Inno installer using PyOxidizer."""
    if not iscc_exe.exists():
        raise Exception("%s does not exist" % iscc_exe)

    inno_build_dir = build_dir / ("inno-pyoxidizer-%s" % target_triple)
    staging_dir = inno_build_dir / "stage"

    inno_build_dir.mkdir(parents=True, exist_ok=True)
    create_pyoxidizer_install_layout(
        source_dir, inno_build_dir, staging_dir, target_triple
    )

    process_install_rules(EXTRA_INSTALL_RULES, source_dir, staging_dir)

    build_installer(
        source_dir,
        inno_build_dir,
        staging_dir,
        iscc_exe,
        version,
        arch="x64" if "x86_64" in target_triple else None,
    )


def build_installer(
    source_dir: pathlib.Path,
    inno_build_dir: pathlib.Path,
    staging_dir: pathlib.Path,
    iscc_exe: pathlib.Path,
    version,
    arch=None,
    suffix="",
):
    """Build an Inno installer from staged Mercurial files.

    This function is agnostic about how to build Mercurial. It just
    cares that Mercurial files are in ``staging_dir``.
    """
    inno_source_dir = source_dir / "contrib" / "packaging" / "inno"

    # The final package layout is simply a mirror of the staging directory.
    package_files = []
    for root, dirs, files in os.walk(staging_dir):
        dirs.sort()

        root = pathlib.Path(root)

        for f in sorted(files):
            full = root / f
            rel = full.relative_to(staging_dir)
            if str(rel.parent) == '.':
                dest_dir = '{app}'
            else:
                dest_dir = '{app}\\%s' % rel.parent

            package_files.append(
                {
                    'source': rel,
                    'dest_dir': dest_dir,
                    'metadata': PACKAGE_FILES_METADATA.get(str(rel), None),
                }
            )

    print('creating installer')

    # Install Inno files by rendering a template.
    jinja_env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(inno_source_dir)),
        # Need to change these to prevent conflict with Inno Setup.
        comment_start_string='{##',
        comment_end_string='##}',
    )

    try:
        template = jinja_env.get_template('mercurial.iss')
    except jinja2.TemplateSyntaxError as e:
        raise Exception(
            'template syntax error at %s:%d: %s'
            % (
                e.name,
                e.lineno,
                e.message,
            )
        )

    content = template.render(package_files=package_files)

    with (inno_build_dir / 'mercurial.iss').open('w', encoding='utf-8') as fh:
        fh.write(content)

    # Copy additional files used by Inno.
    for p in ('mercurial.ico', 'postinstall.txt'):
        shutil.copyfile(
            source_dir / 'contrib' / 'win32' / p, inno_build_dir / p
        )

    args = [str(iscc_exe)]

    if arch:
        args.append('/dARCH=%s' % arch)
        args.append('/dSUFFIX=-%s%s' % (arch, suffix))
    else:
        args.append('/dSUFFIX=-x86%s' % suffix)

    if not version:
        version = read_version_py(source_dir)

    args.append('/dVERSION=%s' % version)
    args.append('/dQUAD_VERSION=%s' % normalize_windows_version(version))

    args.append('/Odist')
    args.append(str(inno_build_dir / 'mercurial.iss'))

    subprocess.run(args, cwd=str(source_dir), check=True)
