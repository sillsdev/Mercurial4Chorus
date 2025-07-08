#!/bin/bash

# Intended to run within docker using image:
#
#  registry.heptapod.net/mercurial/ci-images/core-wheel-x86_64-c:v3.0
#
# we might want to factor most of this with the associated mercurial-core CI
# definition. (i.e. move this script into a place where the CI can directly call it for its purpose)

set -e -x

PYTHON_TARGETS="cp38-cp38 cp39-cp39 cp310-cp310 cp311-cp311 cp312-cp312 cp313-cp313"

# We need to copy the repository to ensure:
# (1) we don't wrongly write roots files in the repository (or any other wrong
#     users)
# (2) we don't reuse pre-compiled extension built outside for manylinux and
#     therefor not compatible.
cp -r /src/ /tmp/src/
cd /tmp/src/
# clear potentially cached artifact from the host
# (we could narrow this purge probably)
hg purge \
    --ignored \
    --no-confirm


if [ ! -e /src/dist/ ]; then
    mkdir -p /src/dist
    chown `stat /src/ -c %u:%g` /src/dist/
fi

for py in $PYTHON_TARGETS; do
    echo 'build wheel for' $py
    # cleanup any previous wheel
    tmp_wd="/tmp/wheels/$py/repaired"
    rm -rf $tmp_wd
    mkdir -p $tmp_wd
    # build a new wheel
    contrib/build-one-linux-wheel.sh $py $tmp_wd
    # fix the owner back to the repository owner
    chown `stat /src/ -c %u:%g` $tmp_wd/*.whl
    mv $tmp_wd/*.whl /src/dist/
done

