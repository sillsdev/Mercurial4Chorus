# ssh.py - Interact with remote SSH servers
#
# Copyright 2019 Gregory Szorc <gregory.szorc@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

# no-check-code because Python 3 native.

import socket
import time
import warnings

from cryptography.utils import CryptographyDeprecationWarning
import paramiko


def wait_for_ssh(hostname, port, timeout=60, username=None, key_filename=None):
    """Wait for an SSH server to start on the specified host and port."""

    class IgnoreHostKeyPolicy(paramiko.MissingHostKeyPolicy):
        def missing_host_key(self, client, hostname, key):
            return

    end_time = time.time() + timeout

    # paramiko triggers a CryptographyDeprecationWarning in the cryptography
    # package. Let's suppress
    with warnings.catch_warnings():
        warnings.filterwarnings(
            'ignore', category=CryptographyDeprecationWarning
        )

        while True:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(IgnoreHostKeyPolicy())
            try:
                client.connect(
                    hostname,
                    port=port,
                    username=username,
                    key_filename=key_filename,
                    timeout=5.0,
                    allow_agent=False,
                    look_for_keys=False,
                )

                return client
            except OSError:
                pass
            except paramiko.AuthenticationException:
                raise
            except paramiko.SSHException:
                pass

            if time.time() >= end_time:
                raise Exception('Timeout reached waiting for SSH')

            time.sleep(1.0)


def exec_command(client, command):
    """exec_command wrapper that combines stderr/stdout and returns channel"""
    chan = client.get_transport().open_session()

    chan.exec_command(command)
    chan.set_combine_stderr(True)

    stdin = chan.makefile('wb', -1)
    stdout = chan.makefile('r', -1)

    return chan, stdin, stdout
