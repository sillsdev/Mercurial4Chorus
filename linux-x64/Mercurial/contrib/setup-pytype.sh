#!/bin/bash

set -e
set -u

# Find the python3 setup that would run pytype
PYTYPE=`which pytype`
PYTHON3=${PYTHON:-`head -n1 ${PYTYPE} | sed -s 's/#!//'`}

# Existing stubs that pytype processes live here
TYPESHED=$(${PYTHON3} -c "import pytype; print(pytype.__path__[0])")/typeshed/stubs
HG_STUBS=${TYPESHED}/mercurial

echo "Patching typeshed at $HG_STUBS"

rm -rf ${HG_STUBS}
mkdir -p ${HG_STUBS}

cat > ${HG_STUBS}/METADATA.toml <<EOF
version = "0.1"
EOF


mkdir -p ${HG_STUBS}/mercurial/cext ${HG_STUBS}/mercurial/thirdparty/attr

touch ${HG_STUBS}/mercurial/__init__.pyi
touch ${HG_STUBS}/mercurial/cext/__init__.pyi
touch ${HG_STUBS}/mercurial/thirdparty/__init__.pyi

ln -sf $(hg root)/mercurial/cext/*.{pyi,typed} \
       ${HG_STUBS}/mercurial/cext
ln -sf $(hg root)/mercurial/thirdparty/attr/*.{pyi,typed} \
       ${HG_STUBS}/mercurial/thirdparty/attr
