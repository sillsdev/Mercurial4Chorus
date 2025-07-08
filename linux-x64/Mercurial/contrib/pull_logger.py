# pull_logger.py - Logs pulls to a JSON-line file in the repo's VFS.
#
# Copyright 2022  Pacien TRAN-GIRARD <pacien.trangirard@pacien.net>
#
# This software may be used and distributed according to the terms of the
# GNU General Public License version 2 or any later version.


'''logs pull parameters to a file

This extension logs the pull parameters, i.e. the remote and common heads,
when pulling from the local repository.

The collected data should give an idea of the state of a pair of repositories
and allow replaying past synchronisations between them. This is particularly
useful for working on data exchange, bundling and caching-related
optimisations.

The record is a JSON-line file located in the repository's VFS at
.hg/pull_log.jsonl.

Log write failures are not considered fatal: log writes may be skipped for any
reason such as insufficient storage or a timeout.

Some basic log file rotation can be enabled by setting 'rotate-size' to a value
greater than 0. This causes the current log file to be moved to
.hg/pull_log.jsonl.rotated when this threshold is met, discarding any
previously rotated log file.

The timeouts of the exclusive lock used when writing to the lock file can be
configured through the 'timeout.lock' and 'timeout.warn' options of this
plugin. Those are not expected to be held for a significant time in practice.::

  [pull-logger]
  timeout.lock = 300
  timeout.warn = 100
  rotate-size = 1kb
'''


import json
import time

from mercurial.i18n import _
from mercurial.utils import stringutil
from mercurial import (
    error,
    extensions,
    lock,
    registrar,
    wireprotov1server,
)

EXT_NAME = b'pull-logger'
EXT_VERSION_CODE = 0

LOG_FILE = b'pull_log.jsonl'
OLD_LOG_FILE = LOG_FILE + b'.rotated'
LOCK_NAME = LOG_FILE + b'.lock'

configtable = {}
configitem = registrar.configitem(configtable)
configitem(EXT_NAME, b'timeout.lock', default=600)
configitem(EXT_NAME, b'timeout.warn', default=120)
configitem(EXT_NAME, b'rotate-size', default=b'100MB')


def wrap_getbundle(orig, repo, proto, others, *args, **kwargs):
    heads, common = extract_pull_heads(others)
    log_entry = {
        'timestamp': time.time(),
        'logger_version': EXT_VERSION_CODE,
        'heads': sorted(heads),
        'common': sorted(common),
    }

    try:
        write_to_log(repo, log_entry)
    except (OSError, error.LockError) as err:
        msg = stringutil.forcebytestr(err)
        repo.ui.warn(_(b'unable to append to pull log: %s\n') % msg)

    return orig(repo, proto, others, *args, **kwargs)


def extract_pull_heads(bundle_args):
    opts = wireprotov1server.options(
        b'getbundle',
        wireprotov1server.wireprototypes.GETBUNDLE_ARGUMENTS.keys(),
        bundle_args.copy(),  # this call consumes the args destructively
    )

    heads = opts.get(b'heads', b'').decode('utf-8').split(' ')
    common = opts.get(b'common', b'').decode('utf-8').split(' ')
    return (heads, common)


def write_to_log(repo, entry):
    locktimeout = repo.ui.configint(EXT_NAME, b'timeout.lock')
    lockwarntimeout = repo.ui.configint(EXT_NAME, b'timeout.warn')
    rotatesize = repo.ui.configbytes(EXT_NAME, b'rotate-size')

    with lock.trylock(
        ui=repo.ui,
        vfs=repo.vfs,
        lockname=LOCK_NAME,
        timeout=locktimeout,
        warntimeout=lockwarntimeout,
    ):
        if rotatesize > 0 and repo.vfs.exists(LOG_FILE):
            if repo.vfs.stat(LOG_FILE).st_size >= rotatesize:
                repo.vfs.rename(LOG_FILE, OLD_LOG_FILE)

        with repo.vfs.open(LOG_FILE, b'a+') as logfile:
            serialised = json.dumps(entry, sort_keys=True)
            logfile.write(serialised.encode('utf-8'))
            logfile.write(b'\n')
            logfile.flush()


def reposetup(ui, repo):
    if repo.local():
        repo._wlockfreeprefix.add(LOG_FILE)
        repo._wlockfreeprefix.add(OLD_LOG_FILE)


def uisetup(ui):
    del wireprotov1server.commands[b'getbundle']
    decorator = wireprotov1server.wireprotocommand(
        name=b'getbundle',
        args=b'*',
        permission=b'pull',
    )

    extensions.wrapfunction(
        container=wireprotov1server,
        funcname='getbundle',
        wrapper=wrap_getbundle,
    )

    decorator(wireprotov1server.getbundle)
