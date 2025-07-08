# __init__.py - High-level automation interfaces
#
# Copyright 2019 Gregory Szorc <gregory.szorc@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

# no-check-code because Python 3 native.

import pathlib
import secrets

from .aws import AWSConnection


class HGAutomation:
    """High-level interface for Mercurial automation.

    Holds global state, provides access to other primitives, etc.
    """

    def __init__(self, state_path: pathlib.Path):
        self.state_path = state_path

        state_path.mkdir(exist_ok=True)

    def default_password(self):
        """Obtain the default password to use for remote machines.

        A new password will be generated if one is not stored.
        """
        p = self.state_path / 'default-password'

        try:
            with p.open('r', encoding='ascii') as fh:
                data = fh.read().strip()

                if data:
                    return data

        except FileNotFoundError:
            pass

        password = secrets.token_urlsafe(24)

        with p.open('w', encoding='ascii') as fh:
            fh.write(password)
            fh.write('\n')

        p.chmod(0o0600)

        return password

    def aws_connection(self, region: str, ensure_ec2_state: bool = True):
        """Obtain an AWSConnection instance bound to a specific region."""

        return AWSConnection(self, region, ensure_ec2_state=ensure_ec2_state)
