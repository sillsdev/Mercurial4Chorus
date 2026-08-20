Requirements
============

Building the Inno installer requires a Windows machine.

The following system dependencies must be installed:

* Inno Setup (http://jrsoftware.org/isdl.php) version 5.4 or newer.
  Be sure to install the optional Inno Setup Preprocessor feature,
  which is required.
* Python 3.8+ (to run the ``packaging.py`` script)

Building
========

The ``packaging.py`` script automates the process of producing an Inno
installer. It manages fetching and configuring non-system dependencies
(such as gettext, and various Python packages).  It can be run from a
basic cmd.exe Window (i.e. activating the MSBuildTools environment is
not required).

From the prompt, change to the Mercurial source directory. e.g.
``cd c:\src\hg``.

Next, invoke ``packaging.py`` to produce an Inno installer.::

   $ py -3 contrib\packaging\packaging.py \
       inno --pyoxidizer-target x86_64-pc-windows-msvc

If everything runs as intended, dependencies will be fetched and
configured into the ``build`` sub-directory, Mercurial will be built,
and an installer placed in the ``dist`` sub-directory. The final line
of output should print the name of the generated installer.

Additional options may be configured. Run ``packaging.py inno --help``
to see a list of program flags.

MinGW
=====

It is theoretically possible to generate an installer that uses
MinGW. This isn't well tested and ``packaging.py`` and may properly
support it. See old versions of this file in version control for
potentially useful hints as to how to achieve this.
