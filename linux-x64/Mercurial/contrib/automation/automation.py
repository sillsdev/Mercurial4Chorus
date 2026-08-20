#!/usr/bin/env python3
#
# automation.py - Perform tasks on remote machines
#
# Copyright 2019 Gregory Szorc <gregory.szorc@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

import os
import pathlib
import subprocess
import sys
import venv


HERE = pathlib.Path(os.path.abspath(__file__)).parent
REQUIREMENTS_TXT = HERE / 'requirements.txt'
SOURCE_DIR = HERE.parent.parent
VENV = SOURCE_DIR / 'build' / 'venv-automation'


def bootstrap():
    venv_created = not VENV.exists()

    VENV.parent.mkdir(exist_ok=True)

    venv.create(VENV, with_pip=True)

    if os.name == 'nt':
        venv_bin = VENV / 'Scripts'
        pip = venv_bin / 'pip.exe'
        python = venv_bin / 'python.exe'
    else:
        venv_bin = VENV / 'bin'
        pip = venv_bin / 'pip'
        python = venv_bin / 'python'

    args = [
        str(pip),
        'install',
        '-r',
        str(REQUIREMENTS_TXT),
        '--disable-pip-version-check',
    ]

    if not venv_created:
        args.append('-q')

    subprocess.run(args, check=True)

    os.environ['HGAUTOMATION_BOOTSTRAPPED'] = '1'
    os.environ['PATH'] = '%s%s%s' % (venv_bin, os.pathsep, os.environ['PATH'])

    subprocess.run([str(python), __file__] + sys.argv[1:], check=True)


def run():
    import hgautomation.cli as cli

    # Need to strip off main Python executable.
    cli.main()


if __name__ == '__main__':
    try:
        if 'HGAUTOMATION_BOOTSTRAPPED' not in os.environ:
            bootstrap()
        else:
            run()
    except subprocess.CalledProcessError as e:
        sys.exit(e.returncode)
    except KeyboardInterrupt:
        sys.exit(1)
