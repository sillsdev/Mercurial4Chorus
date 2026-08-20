# winrm.py - Interact with Windows Remote Management (WinRM)
#
# Copyright 2019 Gregory Szorc <gregory.szorc@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

# no-check-code because Python 3 native.

import logging
import pprint
import time

from pypsrp.client import Client
from pypsrp.powershell import (
    PowerShell,
    PSInvocationState,
    RunspacePool,
)
import requests.exceptions


logger = logging.getLogger(__name__)


def wait_for_winrm(host, username, password, timeout=180, ssl=False):
    """Wait for the Windows Remoting (WinRM) service to become available.

    Returns a ``psrpclient.Client`` instance.
    """

    end_time = time.time() + timeout

    while True:
        try:
            client = Client(
                host,
                username=username,
                password=password,
                ssl=ssl,
                connection_timeout=5,
            )
            client.execute_ps("Write-Host 'Hello, World!'")
            return client
        except requests.exceptions.ConnectionError:
            if time.time() >= end_time:
                raise

            time.sleep(1)


def format_object(o):
    if isinstance(o, str):
        return o

    try:
        o = str(o)
    except (AttributeError, TypeError):
        o = pprint.pformat(o.extended_properties)

    return o


def run_powershell(client, script):
    with RunspacePool(client.wsman) as pool:
        ps = PowerShell(pool)
        ps.add_script(script)

        ps.begin_invoke()

        while ps.state == PSInvocationState.RUNNING:
            ps.poll_invoke()
            for o in ps.output:
                print(format_object(o))

            ps.output[:] = []

        ps.end_invoke()

        for o in ps.output:
            print(format_object(o))

        if ps.state == PSInvocationState.FAILED:
            raise Exception(
                'PowerShell execution failed: %s'
                % ' '.join(map(format_object, ps.streams.error))
            )
