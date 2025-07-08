#!/bin/bash
#
# produces two repositories with different common and missing subsets
#
#   $ discovery-helper.sh REPO NBHEADS DEPT
#
# The Goal is to produce two repositories with some common part and some
# exclusive part on each side. Provide a source repository REPO, it will
# produce two repositories REPO-left and REPO-right.
#
# Each repository will be missing some revisions exclusive to NBHEADS of the
# repo topological heads. These heads and revisions exclusive to them (up to
# DEPTH depth) are stripped.
#
# The "left" repository will use the NBHEADS first heads (sorted by
# description). The "right" use the last NBHEADS one.
#
# To find out how many topological heads a repo has, use:
#
#   $ hg heads -t -T '{rev}\n' | wc -l
#
# Example:
#
#  The `pypy-2018-09-01` repository has 192 heads. To produce two repositories
#  with 92 common heads and ~50 exclusive heads on each side.
#
#    $ ./discovery-helper.sh pypy-2018-08-01 50 10

set -euo pipefail

printusage () {
     echo "usage: `basename $0` REPO NBHEADS DEPTH [left|right]" >&2
}

if [ $# -lt 3 ]; then
    printusage
    exit 64
fi

repo="$1"
shift

nbheads="$1"
shift

depth="$1"
shift

doleft=1
doright=1
if [ $# -gt 1 ]; then
    printusage
    exit 64
elif [ $# -eq 1 ]; then
    if [ "$1" == "left" ]; then
        doleft=1
        doright=0
    elif [ "$1" == "right" ]; then
        doleft=0
        doright=1
    else
        printusage
        exit 64
    fi
fi

leftrepo="${repo}-${nbheads}h-${depth}d-left"
rightrepo="${repo}-${nbheads}h-${depth}d-right"

left="first(sort(heads(all()), 'desc'), $nbheads)"
right="last(sort(heads(all()), 'desc'), $nbheads)"

leftsubset="ancestors($left, $depth) and only($left, heads(all() - $left))"
rightsubset="ancestors($right, $depth) and only($right, heads(all() - $right))"

echo '### creating left/right repositories with missing changesets:'
if [ $doleft -eq 1 ]; then
    echo '# left  revset:' '"'${leftsubset}'"'
fi
if [ $doright -eq 1 ]; then
    echo '# right revset:' '"'${rightsubset}'"'
fi

buildone() {
    side="$1"
    dest="$2"
    revset="$3"
    echo "### building $side repository: $dest"
    if [ -e "$dest" ]; then
        echo "destination repo already exists: $dest" >&2
        exit 1
    fi
    echo '# cloning'
    if ! cp --recursive --reflink=always ${repo} ${dest}; then
        hg clone --noupdate "${repo}" "${dest}"
    fi
    echo '# stripping' '"'${revset}'"'
    hg -R "${dest}" --config extensions.strip= strip --rev "$revset" --no-backup
}

if [ $doleft -eq 1 ]; then
    buildone left "$leftrepo" "$leftsubset"
fi

if [ $doright -eq 1 ]; then
    buildone right "$rightrepo" "$rightsubset"
fi
