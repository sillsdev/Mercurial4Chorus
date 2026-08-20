#!/bin/bash
set -eu

if [[ "$PHABRICATOR_TOKEN" == "NO-PHAB" ]]; then
    echo 'Skipping Phabricator Step' >&2
    exit 0
fi

revision_in_stack=`hg log \
    --rev '.#stack and ::. and topic()' \
    -T '\nONE-REV\n' \
    | grep 'ONE-REV' | wc -l`
revision_on_phab=`hg log \
    --rev '.#stack and ::. and topic() and desc("re:\nDifferential Revision: [^\n]+D\d+$")'\
    -T '\nONE-REV\n' \
    | grep 'ONE-REV' | wc -l`

if [[ $revision_in_stack -eq 0 ]]; then
    echo "stack is empty" >&2
    exit 0
fi

if [[ $revision_on_phab -eq 0 ]]; then
    echo "no tracked diff in this stack" >&2
    exit 0
fi

if [[ $revision_on_phab -lt $revision_in_stack ]]; then
    echo "not all stack changesets (${revision_in_stack}) have matching Phabricator Diff (${revision_on_phab})" >&2
    exit 2
fi

if [[ "$PHABRICATOR_TOKEN" == "" ]]; then
    echo 'missing $PHABRICATOR_TOKEN variable' >&2
    echo '(use PHABRICATOR_TOKEN="NO-PHAB" to disable this step)' >&2
    exit 2
fi

hg \
--config extensions.phabricator= \
--config phabricator.url=https://phab.mercurial-scm.org/ \
--config phabricator.callsign=HG \
--config auth.phabricator.schemes=https \
--config auth.phabricator.prefix=phab.mercurial-scm.org \
--config auth.phabricator.phabtoken=$PHABRICATOR_TOKEN \
phabsend --rev '.#stack and ::. and topic()' \
"$@"
