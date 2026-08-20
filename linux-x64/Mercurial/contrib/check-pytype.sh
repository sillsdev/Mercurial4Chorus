#!/bin/bash

set -e
set -u
set -o pipefail

cd "$(hg root)"

printf "pytype version: "
pytype --version

# Many of the individual files that are excluded here confuse pytype
# because they do a mix of Python 2 and Python 3 things
# conditionally. There's no good way to help it out with that as far as
# I can tell, so let's just hide those files from it for now. We should
# endeavor to empty this list out over time, as some of these are
# probably hiding real problems.
#
# hgext/absorb.py               # [attribute-error]
# hgext/bugzilla.py             # [pyi-error], [attribute-error]
# hgext/convert/bzr.py          # [attribute-error]
# hgext/convert/cvs.py          # [attribute-error], [wrong-arg-types]
# hgext/convert/cvsps.py        # [attribute-error]
# hgext/convert/p4.py           # [wrong-arg-types] (__file: mercurial.utils.procutil._pfile -> IO)
# hgext/convert/subversion.py   # [attribute-error], [name-error], [pyi-error]
# hgext/fastannotate/context.py # no linelog.copyfrom()
# hgext/fastannotate/formatter.py  # [unsupported-operands]
# hgext/fsmonitor/__init__.py   # [name-error]
# hgext/git/__init__.py         # [attribute-error]
# hgext/githelp.py              # [attribute-error] [wrong-arg-types]
# hgext/hgk.py                  # [attribute-error]
# hgext/histedit.py             # [attribute-error], [wrong-arg-types]
# hgext/keyword.py              # [attribute-error]
# hgext/largefiles/storefactory.py  # [attribute-error]
# hgext/lfs/__init__.py         # [attribute-error]
# hgext/narrow/narrowbundle2.py # [attribute-error]
# hgext/narrow/narrowcommands.py    # [attribute-error], [name-error]
# hgext/rebase.py               # [attribute-error]
# hgext/remotefilelog/basepack.py   # [attribute-error], [wrong-arg-count]
# hgext/remotefilelog/basestore.py  # [attribute-error]
# hgext/remotefilelog/contentstore.py   # [missing-parameter], [wrong-keyword-args], [attribute-error]
# hgext/remotefilelog/fileserverclient.py  # [attribute-error]
# hgext/remotefilelog/shallowbundle.py     # [attribute-error]
# hgext/remotefilelog/remotefilectx.py  # [module-attr] (This is an actual bug)
# hgext/zeroconf/__init__.py    # bytes vs str; tests fail on macOS
#
# mercurial/context.py          # many [attribute-error]
# mercurial/crecord.py          # tons of [attribute-error], [module-attr]
# mercurial/debugcommands.py    # [wrong-arg-types]
# mercurial/dispatch.py         # initstdio: No attribute ... on TextIO [attribute-error]
# mercurial/exchange.py         # [attribute-error]
# mercurial/hgweb/hgweb_mod.py  # [attribute-error], [name-error], [wrong-arg-types]
# mercurial/hgweb/server.py     # [attribute-error], [name-error], [module-attr]
# mercurial/hgweb/wsgicgi.py    # confused values in os.environ
# mercurial/httppeer.py         # [attribute-error], [wrong-arg-types]
# mercurial/keepalive.py        # [attribute-error]
# mercurial/localrepo.py        # [attribute-error]
# mercurial/minirst.py          # [unsupported-operands], [attribute-error]
# mercurial/repoview.py         # [attribute-error]
# mercurial/testing/storage.py  # tons of [attribute-error]
# mercurial/win32.py            # [not-callable]
# mercurial/wireprotov1server.py  # BUG?: BundleValueError handler accesses subclass's attrs

# TODO: use --no-cache on test server?  Caching the files locally helps during
#       development, but may be a hinderance for CI testing.

# TODO: include hgext and hgext3rd

# use ts to produce some timing if available
if ! command -v ts; then
    ts() {
        cat
    }
fi

pytype --keep-going --jobs auto \
    doc/check-seclevel.py hgdemandimport hgext mercurial \
    -x hgext/absorb.py \
    -x hgext/bugzilla.py \
    -x hgext/convert/bzr.py \
    -x hgext/convert/cvs.py \
    -x hgext/convert/cvsps.py \
    -x hgext/convert/p4.py \
    -x hgext/convert/subversion.py \
    -x hgext/fastannotate/context.py \
    -x hgext/fastannotate/formatter.py \
    -x hgext/fsmonitor/__init__.py \
    -x hgext/git/__init__.py \
    -x hgext/githelp.py \
    -x hgext/hgk.py \
    -x hgext/histedit.py \
    -x hgext/keyword.py \
    -x hgext/largefiles/storefactory.py \
    -x hgext/lfs/__init__.py \
    -x hgext/narrow/narrowbundle2.py \
    -x hgext/narrow/narrowcommands.py \
    -x hgext/rebase.py \
    -x hgext/remotefilelog/basepack.py \
    -x hgext/remotefilelog/basestore.py \
    -x hgext/remotefilelog/contentstore.py \
    -x hgext/remotefilelog/fileserverclient.py \
    -x hgext/remotefilelog/remotefilectx.py \
    -x hgext/remotefilelog/shallowbundle.py \
    -x hgext/zeroconf/__init__.py \
    -x mercurial/context.py \
    -x mercurial/crecord.py \
    -x mercurial/debugcommands.py \
    -x mercurial/dispatch.py \
    -x mercurial/exchange.py \
    -x mercurial/hgweb/hgweb_mod.py \
    -x mercurial/hgweb/server.py \
    -x mercurial/hgweb/wsgicgi.py \
    -x mercurial/httppeer.py \
    -x mercurial/keepalive.py \
    -x mercurial/localrepo.py \
    -x mercurial/minirst.py \
    -x mercurial/repoview.py \
    -x mercurial/testing/storage.py \
    -x mercurial/thirdparty \
    -x mercurial/win32.py \
    -x mercurial/wireprotov1server.py \
    | ts -i "(%.s)" | ts -s "%.s"

if find .pytype/pyi -name '*.pyi' | xargs grep -ql '# Caught error'; then
    echo 'pytype crashed while generating the following type stubs:'
    find .pytype/pyi -name '*.pyi' | xargs grep -l '# Caught error' | sort
fi
