# Extract version number into 4 parts, some of which may be empty:
#
# version: the numeric part of the most recent tag. Will always look like 1.3.
#
# type: if an rc build, "rc", otherwise empty
#
# distance: the distance from the nearest tag, or empty if built from a tag
#
# node: the node|short hg was built from, or empty if built from a tag
gethgversion() {
    # allow passing the version from an environment variable
    # in case of builds on an older platform that cannot build hg
    if [ ! -z "$USELOCALHG" ]; then
        HG="$(which hg)"
        version=$($HG log -r . --template "{latesttag}")
        distance=$($HG log -r . --template "{latesttagdistance}")
        node=$($HG log -r . --template "{node|short}")
        return
    fi
    if [ -z "${1+x}" ]; then
        python="python3"
    else
        python="$1"
    fi
    export HGRCPATH=
    export HGPLAIN=

    make cleanbutpackages PYTHON=$python
    make local PURE=--pure PYTHON=$python
    HG="$PWD/hg"

    $python "$HG" version > /dev/null || { echo 'abort: hg version failed!'; exit 1 ; }

    hgversion=`LANGUAGE=C $python "$HG" version | sed -ne 's/.*(version \(.*\))$/\1/p'`

    if echo $hgversion | grep + > /dev/null 2>&1 ; then
        tmp=`echo $hgversion | cut -d+ -f 2`
        hgversion=`echo $hgversion | cut -d+ -f 1`
        distance=`echo $tmp | cut -d- -f 1`
        node=`echo $tmp | cut -d- -f 2`
    else
        distance=''
        node=''
    fi
    if echo $hgversion | grep -E -- '[0-9]\.[0-9](\.[0-9])?rc' > /dev/null 2>&1; then
        version=`echo $hgversion | cut -d'r' -f1`
        type="rc`echo $hgversion | cut -d'c' -f2-`"
    else
        version=$hgversion
        type=''
    fi
}
