WiX Installer
=============

The files in this directory are used to produce an MSI installer using
the WiX Toolset (http://wixtoolset.org/).

The MSI installers require elevated (admin) privileges due to the
installation of MSVC CRT libraries into the Windows system store. See
the Inno Setup installers in the ``inno`` sibling directory for installers
that do not have this requirement.

Requirements
============

Building the WiX installer requires a Windows machine.

The following system dependencies must be installed:

* Python 3.8+ (to run the ``packaging.py`` script)

Building
========

The ``packaging.py`` script automates the process of producing an MSI
installer. It manages fetching and configuring non-system dependencies
(such as gettext, and various Python packages).  It can be run from a
basic cmd.exe Window (i.e. activating the MSBuildTools environment is
not required).

From the prompt, change to the Mercurial source directory. e.g.
``cd c:\src\hg``.

Next, invoke ``packaging.py`` to produce an MSI installer.::

   $ py -3 contrib\packaging\packaging.py \
       wix --pyoxidizer-target x86_64-pc-windows-msvc

If everything runs as intended, dependencies will be fetched and
configured into the ``build`` sub-directory, Mercurial will be built,
and an installer placed in the ``dist`` sub-directory. The final line
of output should print the name of the generated installer.

Additional options may be configured. Run ``packaging.py wix --help``
to see a list of program flags.

Relationship to TortoiseHG
==========================

TortoiseHG uses the WiX files in this directory.

The code for building TortoiseHG installers lives at
https://foss.heptapod.net/mercurial/tortoisehg/thg-winbuild and is maintained by
Steve Borho (steve@borho.org).

When changing behavior of the WiX installer, be sure to notify
the TortoiseHG Project of the changes so they have ample time
provide feedback and react to those changes.
