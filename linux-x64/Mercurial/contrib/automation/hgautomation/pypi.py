# pypi.py - Automation around PyPI
#
# Copyright 2019 Gregory Szorc <gregory.szorc@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

# no-check-code because Python 3 native.

from twine.commands.upload import upload as twine_upload
from twine.settings import Settings


def upload(paths):
    """Upload files to PyPI.

    `paths` is an iterable of `pathlib.Path`.
    """
    settings = Settings()

    twine_upload(settings, [str(p) for p in paths])
