# try_server.py - Interact with Try server
#
# Copyright 2019 Gregory Szorc <gregory.szorc@gmail.com>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.

# no-check-code because Python 3 native.

import base64
import json
import os
import subprocess
import tempfile

from .aws import AWSConnection

LAMBDA_FUNCTION = "ci-try-server-upload"


def trigger_try(c: AWSConnection, rev="."):
    """Trigger a new Try run."""
    lambda_client = c.session.client("lambda")

    cset, bundle = generate_bundle(rev=rev)

    payload = {
        "bundle": base64.b64encode(bundle).decode("utf-8"),
        "node": cset["node"],
        "branch": cset["branch"],
        "user": cset["user"],
        "message": cset["desc"],
    }

    print("resolved revision:")
    print("node: %s" % cset["node"])
    print("branch: %s" % cset["branch"])
    print("user: %s" % cset["user"])
    print("desc: %s" % cset["desc"].splitlines()[0])
    print()

    print("sending to Try...")
    res = lambda_client.invoke(
        FunctionName=LAMBDA_FUNCTION,
        InvocationType="RequestResponse",
        Payload=json.dumps(payload).encode("utf-8"),
    )

    body = json.load(res["Payload"])
    for message in body:
        print("remote: %s" % message)


def generate_bundle(rev="."):
    """Generate a bundle suitable for use by the Try service.

    Returns a tuple of revision metadata and raw Mercurial bundle data.
    """
    # `hg bundle` doesn't support streaming to stdout. So we use a temporary
    # file.
    path = None
    try:
        fd, path = tempfile.mkstemp(prefix="hg-bundle-", suffix=".hg")
        os.close(fd)

        args = [
            "hg",
            "bundle",
            "--type",
            "gzip-v2",
            "--base",
            "public()",
            "--rev",
            rev,
            path,
        ]

        print("generating bundle...")
        subprocess.run(args, check=True)

        with open(path, "rb") as fh:
            bundle_data = fh.read()

    finally:
        if path:
            os.unlink(path)

    args = [
        "hg",
        "log",
        "-r",
        rev,
        # We have to upload as JSON, so it won't matter if we emit binary
        # since we need to normalize to UTF-8.
        "-T",
        "json",
    ]
    res = subprocess.run(args, check=True, capture_output=True)
    return json.loads(res.stdout)[0], bundle_data
