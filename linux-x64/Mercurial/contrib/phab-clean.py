#!/usr/bin/env python3
#
# A small script to automatically reject idle Diffs
#
# you need to set the PHABBOT_USER and PHABBOT_TOKEN environment variable for authentication

import datetime
import os
import sys

import phabricator

MESSAGE = """There seems to have been no activities on this Diff for the past 3 Months.

By policy, we are automatically moving it out of the `need-review` state.

Please, move it back to `need-review` without hesitation if this diff should still be discussed.

:baymax:need-review-idle:
"""


PHAB_URL = "https://phab.mercurial-scm.org/api/"
USER = os.environ.get("PHABBOT_USER", "baymax")
TOKEN = os.environ.get("PHABBOT_TOKEN")


NOW = datetime.datetime.now()

# 3 months in seconds
DELAY = 60 * 60 * 24 * 30 * 3


def get_all_diff(phab):
    """Fetch all the diff that the need review"""
    return phab.differential.query(
        status="status-needs-review",
        order="order-modified",
        paths=[('HG', None)],
    )


def filter_diffs(diffs, older_than):
    """filter diffs to only keep the one unmodified sin <older_than> seconds"""
    olds = []
    for d in diffs:
        modified = int(d['dateModified'])
        modified = datetime.datetime.fromtimestamp(modified)
        d["idleFor"] = idle_for = NOW - modified
        if idle_for.total_seconds() > older_than:
            olds.append(d)
    return olds


def nudge_diff(phab, diff):
    """Comment on the idle diff and reject it"""
    diff_id = int(d['id'])
    phab.differential.createcomment(
        revision_id=diff_id, message=MESSAGE, action="reject"
    )


if not USER:
    print(
        "not user specified please set PHABBOT_USER and PHABBOT_TOKEN",
        file=sys.stderr,
    )
elif not TOKEN:
    print(
        "not api-token specified please set PHABBOT_USER and PHABBOT_TOKEN",
        file=sys.stderr,
    )
    sys.exit(1)

phab = phabricator.Phabricator(USER, host=PHAB_URL, token=TOKEN)
phab.connect()
phab.update_interfaces()
print('Hello "%s".' % phab.user.whoami()['realName'])

diffs = get_all_diff(phab)
print("Found %d Diffs" % len(diffs))
olds = filter_diffs(diffs, DELAY)
print("Found %d old Diffs" % len(olds))
for d in olds:
    diff_id = d['id']
    status = d['statusName']
    modified = int(d['dateModified'])
    idle_for = d["idleFor"]
    msg = 'nudging D%s in "%s" state for %s'
    print(msg % (diff_id, status, idle_for))
    # uncomment to actually affect phab
    nudge_diff(phab, d)
