%global emacs_lispdir %{_datadir}/emacs/site-lisp

%define withpython %{nil}

%global pythonexe python3

%if "%{?withpython}"
%global pythonver %{withpython}
%global pythonname Python-%{withpython}
%global pythonhg python-hg
%global hgpyprefix /opt/%{pythonhg}
# byte compilation will fail on some some Python /test/ files
%global _python_bytecompile_errors_terminate_build 0

%else
%global pythonver %(%{pythonexe} -c 'import sys;print(".".join(map(str, sys.version_info[:2])))')

%endif

Summary: A fast, lightweight Source Control Management system
Name: mercurial
Version: snapshot
Release: 0
License: GPLv2+
Prefix: /
Group: Development/Tools
URL: https://mercurial-scm.org/
Source0: %{name}-%{version}-%{release}.tar.gz
%if "%{?withpython}"
Source1: %{pythonname}.tgz
%endif
BuildRoot: %{_tmppath}/%{name}-%{version}-%{release}-root

BuildRequires: make, gcc, gettext
%if "%{?withpython}"
BuildRequires: readline-devel, openssl-devel, ncurses-devel, zlib-devel, bzip2-devel
%else
BuildRequires: %{pythonexe} >= %{pythonver}, %{pythonexe}-devel, python3-docutils
Requires: %{pythonexe} >= %{pythonver}
%endif
# The hgk extension uses the wish tcl interpreter, but we don't enforce it
#Requires: tk

%description
Mercurial is a fast, lightweight source control management system designed
for efficient handling of very large distributed projects.

%prep

%if "%{?withpython}"
%setup -q -n mercurial-%{version}-%{release} -a1
%else
%setup -q -n mercurial-%{version}-%{release}
%endif

%build

export HGPYTHON3=1

%if "%{?withpython}"

PYPATH=$PWD/%{pythonname}
PYTHON_FULLPATH=$PYPATH/python3
cd $PYPATH
./configure --prefix=%{hgpyprefix} --with-ensurepip=install --enable-optimizations --with-lto
make all %{?_smp_mflags}
# add a symlink and only refer to python3 from here on
ln -s python python3
# remove python reference
sed -i 's|#!/usr/bin/env python|#!/usr/bin/env python3|' Lib/encodings/rot_13.py
$PYTHON_FULLPATH -m ensurepip --default-pip
$PYTHON_FULLPATH -m pip install setuptools setuptools-scm docutils
cd -

# verify Python environment
LD_LIBRARY_PATH=$PYPATH $PYTHON_FULLPATH -c 'import sys, zlib, bz2, ssl, curses, readline'
LD_LIBRARY_PATH=$PYPATH $PYTHON_FULLPATH -c "import ssl; print(ssl.HAS_TLSv1_2)"
LD_LIBRARY_PATH=$PYPATH $PYTHON_FULLPATH -c "import docutils"

# set environment for make
export PATH=$PYPATH:$PATH
export LD_LIBRARY_PATH=$PYPATH
export CFLAGS="-L $PYPATH"
%else
PYTHON_FULLPATH=$(which python3)
$PYTHON_FULLPATH -m pip install pip setuptools setuptools-scm packaging --upgrade
%endif

make -C contrib/chg

sed -i -e '1s|#!/usr/bin/env python3$|#!/usr/bin/env %{pythonexe}|' contrib/hg-ssh

%install
rm -rf $RPM_BUILD_ROOT

export HGPYTHON3=1

%if "%{?withpython}"

PYPATH=$PWD/%{pythonname}
PYTHON_FULLPATH=$PYPATH/python3
cd $PYPATH
make install DESTDIR=$RPM_BUILD_ROOT
# these .a are not necessary and they are readonly and strip fails - kill them!
rm -f %{buildroot}%{hgpyprefix}/lib/{,python2.*/config}/libpython2.*.a
cd -

PATH=$PYPATH:$PATH LD_LIBRARY_PATH=$PYPATH make install PYTHON=$PYTHON_FULLPATH DESTDIR=$RPM_BUILD_ROOT PIP_PREFIX=$RPM_BUILD_ROOT/%{hgpyprefix} PREFIX=%{hgpyprefix} MANDIR=%{_mandir} PURE="--rust"
mkdir -p $RPM_BUILD_ROOT%{_bindir}
( cd $RPM_BUILD_ROOT%{_bindir}/ && ln -s ../..%{hgpyprefix}/bin/hg . )
pathfix.py -n -i %{hgpyprefix}/bin/python3 $(readlink -f $RPM_BUILD_ROOT%{_bindir}/hg)

%else
PYTHON_FULLPATH=$(which python3)
make install PYTHON=$PYTHON_FULLPATH DESTDIR=$RPM_BUILD_ROOT PREFIX=$RPM_BUILD_ROOT/%{_prefix} MANDIR=%{_mandir} PURE="--rust"

%endif

install -m 755 contrib/chg/chg $RPM_BUILD_ROOT%{_bindir}/
install -m 755 contrib/hgk $RPM_BUILD_ROOT%{_bindir}/
install -m 755 contrib/hg-ssh $RPM_BUILD_ROOT%{_bindir}/

mkdir -p $RPM_BUILD_ROOT%{emacs_lispdir}
install -m 644 contrib/mercurial.el $RPM_BUILD_ROOT%{emacs_lispdir}/
install -m 644 contrib/mq.el $RPM_BUILD_ROOT%{emacs_lispdir}/

mkdir -p $RPM_BUILD_ROOT/%{_sysconfdir}/mercurial/hgrc.d

%clean
rm -rf $RPM_BUILD_ROOT

%files
%defattr(-,root,root,-)
%doc CONTRIBUTORS COPYING doc/README doc/hg*.txt doc/hg*.html *.cgi contrib/*.fcgi contrib/*.wsgi
%doc %attr(644,root,root) %{_mandir}/man?/hg*
%doc %attr(644,root,root) contrib/*.svg
%dir %{_datadir}/emacs/site-lisp/
%{_datadir}/emacs/site-lisp/mercurial.el
%{_datadir}/emacs/site-lisp/mq.el
%{_bindir}/hg
%{_bindir}/chg
%{_bindir}/hgk
%{_bindir}/hg-ssh
%dir %{_sysconfdir}/mercurial
%dir %{_sysconfdir}/mercurial/hgrc.d
%if "%{?withpython}"
%{hgpyprefix}
%else
%{_libdir}/python%{pythonver}/site-packages/mercurial-*.dist-info/
%{_libdir}/python%{pythonver}/site-packages/%{name}
%{_libdir}/python%{pythonver}/site-packages/hgext
%{_libdir}/python%{pythonver}/site-packages/hgext3rd
%{_libdir}/python%{pythonver}/site-packages/hgdemandimport
/usr/share/bash-completion/completions/hg
/usr/share/zsh/site-functions/_hg
%endif
