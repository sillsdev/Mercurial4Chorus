#!/usr/bin/env python3
"""Rebuild win/Mercurial by building Mercurial from source.

MUST BE RUN ON WINDOWS. Needs the following prerequisites:

- Python 3 (run `py` to see if you have it)
- Rust, specifically Rustup (`winget install Rustlang.Rustup)
- PyOxidizer version 0.17.0 (see README-Windows.md to install)
- VS 2022 build tools (winget install Microsoft.VisualStudio.2022.BuildTools)
- Mercurial (winget install Mercurial.Mercurial)
    - Then check if it's on your PATH by running `hg version`
	- If `hg version` fails, restart your command prompt
- .NET SDK 8 or later (e.g. winget install Microsoft.DotNet.SDK.8)
- A clone of the Mercurial source repo *beside* this repo, in `..\\hg`
    - hg clone https://foss.heptapod.net/mercurial/mercurial-devel ..\\hg

Previous builds of SIL.Chorus.Mercurial copied files from TortoiseHg, which
put all the Python files that Mercurial needs into a 19 MB lib\\library.zip
file. This reduced file count but was slow to start up, as the 19 MB file
had to be read each time hg.exe starts up. This build splits those files
off into multiple directories, speeding up startup time for hg.exe.

However, the drawback of this is that the ChorusHub installer needs to track
the component GUIDs assigned by ChorusHub's MSI installer. That was formerly
done with a .guidsForInstaller.xml file in each directory. With 40 different
directories (or a lot more if --no-trim is used!), that's no longer feasible,
so a recent version of SIL.BuildTasks (3.3.0 or later) is needed in order to
consolidate all the .guidsForInstaller.xml files into a single one.

If you re-run this build multiple times, you will notice that many .pyc and
.pyd files appear to change. This is normal: Python uses frozendict and
frozenset objects as part of the process of writing .pyc or .pyd files, which
do not guarantee the order in which objects will be serialized. So although
the files' behavior has not changed, some of their bytes will be in a different
order. This is fine. To help you judge whether a build is truly different, this
script prints a summary of added/removed files at the end.

What SHOULD be checked for is if .guidsForInstaller.all.xml has lost or changed
any lines. New GUIDs are fine, losing or changing pre-existing GUIDs is not.

Usage::

    py -3 build-windows-payload.py                   # build ../hg as checked out
    py -3 build-windows-payload.py --tag 7.0.1       # update it to a tag first
    py -3 build-windows-payload.py --from-stage DIR  # reuse an existing staging tree
    py -3 build-windows-payload.py --no-trim         # useful for debugging a build
"""

from __future__ import annotations

import argparse
import contextlib
import os
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile

# The Mercurial tag this script expects to see in `../hg`. Wil be overridden
# by `--tag`, but if the Mercurial source tree is NOT at this version, script
# will warn you that you might be building a different version thnn you think.
#
# Keep this in sync with MercurialVersion in SIL.Chorus.Mercurial.csproj and
# the mercurial-version matrix in .github/workflows/nuget-ci-cd.yml.
DEFAULT_HG_TAG = "7.0.1"

# Target tripes for PyOxidizer; you usually won't need to change this.
DEFAULT_TARGET_TRIPLE = "x86_64-pc-windows-msvc"
TARGET_TRIPLES = ["i686-pc-windows-msvc", "x86_64-pc-windows-msvc"]

# Files not to delete when cleaning out the old win/Mercurial build
PRESERVE = [
    "mercurial.ini",
    "Mercurial.url",
    "cacert.pem",
    "defaultrc/Paths.rc",
    # Mercurial has not read default.d/ since the 2.x era -- there is not one
    # reference to it anywhere in mercurial/ or hgext/. Preserved only so this
    # script does not quietly delete committed files; dropping the directory
    # outright is a separate decision.
    "default.d/cacerts.rc",
    "default.d/editor.rc",
    "default.d/mergetools.rc",
]

# Two more files to preserve wherever they are found (as opposed to the PRESERVE
# list which specifies full paths relative to the root of the win/Mercurial tree)
# These are used by SIL.BuildTasks to build WiX installers, and must never be
# automatically deleted (though the individual .guidsForInstaller.xml files will
# be able to be deleted once Chorus has switched over to SIL.BuildTasks 3.3.0)
GUID_FILE = ".guidsForInstaller.xml"
CONSOLIDATED_GUID_FILE = ".guidsForInstaller.all.xml"

# 3.3.0 is the first SIL.BuildTasks with MakeWixForDirTree.ConsolidatedGuidFile.
# An earlier version restores fine but fails the build the moment the task is
# invoked, with MSB4064: "The parameter ConsolidatedGuidFile is not supported by
# the MakeWixForDirTree task". --sil-buildtasks-version overrides this when a
# newer release has to be tried.
DEFAULT_SIL_BUILDTASKS_VERSION = "3.3.0"


def _is_guid_file(name: str) -> bool:
    """Is *name* one of the files recording installer GUIDs?"""
    return name == GUID_FILE or name == CONSOLIDATED_GUID_FILE


def die(msg: str) -> None:
    print("error: %s" % msg, file=sys.stderr)
    raise SystemExit(1)


# Files that hgpackaging's staging rules produce but this payload does not
# want. Written as exclusions rather than an allowlist so that a file a later
# Mercurial adds is shipped by default; the added/removed summary printed at
# the end is there to catch anything that slips in that way.

# The two files that only make sense in a standalone Mercurial install:
# ReadMe.html is an HTML index of the doc/ tree this payload drops, and
# ReleaseNotes.txt is contrib/win32/postinstall.txt, the "you may want to add
# hg to PATH" notes the Inno installer shows after installing. Copying.txt is
# deliberately NOT dropped -- it is the licence text, and it already carries a
# GUID from an older payload.
DROP_ROOT_FILES = [
    "ReadMe.html",
    "ReleaseNotes.txt",
]

# Whole directories. Each is a copy, made by hgpackaging's STAGING_RULES_APP,
# of data that also lives under lib/, plus doc/, which is stubbed out below.
# This payload has never shipped any of them: templates/ only matters for
# `hg log --style=X`, and Chorus passes inline --template strings.
#
# Note that this drops only the top-level copies. The originals under
# lib/mercurial/ stay, so `hg help` and configitems.toml still resolve --
# resourceutil reads those through importlib, and only templater looks in the
# top-level copy.
DROP_DIRECTORIES = [
    "doc",
    "helptext",
    "locale",
    "templates",
]

# Exact paths to leave out. Empty, and worth keeping empty: everything else
# hgpackaging stages is a file Mercurial's own installers ship.
#
# The predecessor of this script dropped contrib/mq.el, because TortoiseHg's
# WiX allowlist did not carry it into the MSI even though its staging tree had
# it. Mercurial's installers do ship it, so it ships here now.
DROP_FILES = []

# Nothing under lib/ is dropped by the rules above. It is the frozen
# application's own module tree -- an index of concrete paths baked into hg.exe
# -- and pruning pieces of it is how a frozen application breaks. The trimming
# below is the deliberate, narrower exception; see there.


def is_dropped(relative: str) -> bool:
    """Should this staging-tree path be left out of the payload?"""
    if relative in DROP_FILES:
        return True
    if relative.split("/")[0] in DROP_DIRECTORIES:
        return True
    if "/" not in relative:
        return relative in DROP_ROOT_FILES
    return False


# ---------------------------------------------------------------------------
# Trimming
#
# Everything below is a file Mercurial's own installers ship and this package
# has no way to reach. It is separated from DROP_* above because the reasoning
# is different in kind: those rules are about the shape of the install layout,
# these are about what Chorus does with it.
#
# The motivation is size, and the numbers below are measured by running this
# script's --from-stage over the official Mercurial 7.0.1 x64 MSI unpacked with
# msiextract. The nupkg column is deflate calibrated against a real dotnet pack,
# so it is accurate to a few percent rather than exact.
#
#     mode                                 files  dirs   raw     nupkg
#     --no-trim                             3277   347   99.8 MB  37.3 MB
#     --no-trim-hgext --no-trim-sources     1520    99   75.8 MB  28.7 MB
#     --no-trim-hgext                        837    65   63.3 MB  25.3 MB
#     --no-trim-sources                      675    58   60.2 MB  23.6 MB
#     (default)                              395    40   54.0 MB  21.9 MB
#     TortoiseHg                              99     6   46.3 MB  23.4 MB  <- replaced
#
# The directory count matters as much as the megabytes: it is the number of
# .guidsForInstaller.xml files that have to be maintained for the life of the
# product, and directories created once can never be cleanly retired.
#
# Everything is trimmed by default now. The rule for what may go in
# TRIM_UNIMPORTED_*, the set that cannot be turned off individually: a grep over
# the shipped mercurial/ and hgext/ trees finds NO import of it, or finds only
# imports guarded by try/except. Anything whose only importer is a real (if
# unused) code path belongs under TRIM_HGEXT, which --no-trim-hgext can restore.
#
# Verify with:
#     grep -rl "import <name>" lib/mercurial lib/hgext --include=*.py

# Third-party top-level entries under lib/ that nothing in the payload imports.
# rust/hgcli/pyoxidizer.bzl pip-installs contrib/packaging/requirements-windows-py3.txt
# on Windows "for convenience"; most of what that pulls in is Mercurial's own
# test and release tooling rather than anything hg uses at run time.
TRIM_UNIMPORTED_PACKAGES = {
    # pytest, vcrpy and their dependency closure. requirements-windows.txt.in
    # asks for pytest-vcr with the comment "Needed by the phabricator tests".
    "_pytest", "pytest", "py", "pluggy", "iniconfig", "toml", "atomicwrites",
    "colorama", "vcr", "yarl", "multidict", "yaml", "_yaml", "wrapt", "attr",
    "urllib3", "idna", "build", "packaging", "importlib_metadata", "zipp",
    # "Needed by the release note tooling"
    "fuzzywuzzy",
    # console scripts pip generated: pytest.exe, pygmentize.exe, dulwich.exe...
    "bin",
    # only setup.py build_doc imports docutils, and that is stubbed out here
    "docutils",
    # hg-git's library. hgext/git uses pygit2 instead, and nothing in the
    # payload imports dulwich at all.
    "dulwich",
    # for the third-party mercurial_keyring extension, which is not shipped
    "keyring", "win32ctypes",
}

# Same, for entries that are a single file rather than a package.
TRIM_UNIMPORTED_FILES = {
    "pytest_vcr.py", "pyparsing.py", "zipp.py", "six.py",
    "typing_extensions.py", "cached_property.py",
    # tcl/tk. Nothing in the payload imports tkinter; these are here because
    # the CPython distribution PyOxidizer embeds carries them.
    "tcl86t.dll", "tk86t.dll", "_tkinter.pyd",
    # stdlib extension modules with no importer here
    "_msi.pyd", "winsound.pyd", "_zoneinfo.pyd",
}

# Matched against the start of the name, because the CPython ABI tag in a
# .pyd filename moves with the embedded interpreter version.
#
# _curses is the exception to "no importer": mercurial/color.py,
# mercurial/crecord.py and hgext/histedit.py all import it, and all three do so
# inside a try/except that falls back cleanly. crecord is the `hg commit -i`
# chunk-selection UI, which Chorus never invokes; color.py loses terminfo
# lookup, which does nothing on Windows anyway.
TRIM_UNIMPORTED_PREFIXES = ("_curses",)

# Data under lib/mercurial/ that Mercurial does not read from there. Both are
# copies STAGING_RULES_APP makes at the top level, and the top-level copy is
# the live one: i18n.py resolves locale through os.path.join(datapath,
# 'locale') and templater.templatedir() through datapath + b'templates', where
# datapath is the directory holding hg.exe. DROP_DIRECTORIES already removes
# those live copies, so these are dead weight twice over.
#
# lib/mercurial/helptext is deliberately NOT here. That one is live: help.py
# reads it with open_resource(b'mercurial.helptext', ...), which resolves
# through importlib to lib/, so trimming it would break `hg help <topic>`.
TRIM_DEAD_DATA = {"locale", "templates"}

# hgext extensions Chorus never enables, and the third-party packages whose
# only importer is one of them. Trimmed by default, restored by
# --no-trim-hgext.
#
# These are live code paths rather than dead weight, so this was opt-in at
# first. It became the default once the full LibChorus test suite passed on
# Windows against a payload built this way -- that suite exercises clone, pull,
# push, commit, merge, update and log, which is the ground truth this package
# exists to serve. What remains reachable is a config file outside this package
# enabling one of these extensions, which is what --no-trim-hgext is for.
#
# Chorus enables exactly eol, hgext.graphlog and convert, in the payload's own
# mercurial.ini, plus the vendored fixutf8. All four are kept, as is everything
# not named here -- a denylist, so an extension a later Mercurial adds ships by
# default.
TRIM_HGEXT = {
    "absorb", "acl", "beautifygraph", "blackbox", "bookflow", "bugzilla",
    "censor", "children", "churn", "clonebundles", "closehead",
    "commitextras", "extdiff", "factotum", "fastannotate", "fix", "fsmonitor",
    "git", "githelp", "gpg", "hgk", "highlight", "histedit", "hooklib",
    "journal", "keyword", "largefiles", "lfs", "logtoprocess", "mq", "narrow",
    "notify", "patchbomb", "phabricator", "purge", "rebase", "record",
    "relink", "releasenotes", "remotefilelog", "schemes", "share", "show",
    "sparse", "split", "sqlitestore", "strip", "transplant", "uncommit",
    "win32mbcs", "win32text", "zeroconf",
}

# Reached only from an extension in TRIM_HGEXT, so they go with it:
# hgext/highlight/highlight.py is the only importer of pygments, and
# hgext/git/gitutil.py the only importer of pygit2. cffi and pycparser are
# pygit2's dependencies.
TRIM_HGEXT_PACKAGES = {"pygments", "pygit2", "cffi", "pycparser"}
TRIM_HGEXT_PREFIXES = ("_cffi_backend",)

# Deliberately empty, and libffi-7.dll must never be put back in it.
#
# It was here once, on the assumption that a library named libffi belonged to
# cffi. It does not: lib/_ctypes.pyd is the only thing in the payload that
# imports it, and _ctypes is what the stdlib ctypes module is built on.
# mercurial/win32.py imports ctypes at module scope, so trimming libffi-7.dll
# produced an hg.exe that could not run any command at all:
#
#     File "mercurial.win32", line 11, in <module>
#     ImportError: DLL load failed while importing _ctypes:
#                  The specified module could not be found.
#
# check_native_dependencies() below now catches this at build time. Adding a
# DLL here needs a reason from its import table, not from its name.
TRIM_HGEXT_FILES: set = set()


# Python source, dropped by default and restored by --no-trim-sources.
#
# hg.exe does not need it. Its resource index carries a separate path for each
# module's source and its bytecode -- lib\\mercurial\\util.py in one blob
# section, lib\\mercurial\\__pycache__\\util.cpython-39.pyc in another (both
# UTF-16, which is why grepping the binary for them as ASCII finds nothing) --
# and the bytecode is PEP 552 unchecked-hash, so nothing ever validates it
# against the source it came from.
#
# What is lost is source lines in tracebacks: an hg crash still reports the
# file, line and function, but the offending line itself is blank. Chorus
# surfaces hg's stderr, so that is a real if modest cost to diagnosis, which is
# what --no-trim-sources buys back when a crash needs investigating.
#
# The rule enforced below is that a .py is removed only when its bytecode is
# actually present. Against the official 7.0.1 x64 MSI the pairing is exact --
# 1266 .py, 1266 .pyc, no orphan on either side -- but a Mercurial that ships a
# module PyOxidizer does not compile would otherwise be made unimportable, so
# the check is on the removal rather than on a count.


def _staged_both_spellings(stage: pathlib.Path) -> list:
    """STAGE_RENAMES pairs the staging tree really holds under both names.

    Compares directory entries. Testing each path with is_file() looks like the
    obvious way to do this and is wrong: stat() is case-insensitive on Windows
    and on macOS, so both spellings answer True whenever either file exists,
    and a guard built that way fires on every run instead of never. Reading the
    directory gives the names actually on disk.
    """
    both = []
    for staged, shipped in STAGE_RENAMES.items():
        directory, staged_name = staged.rsplit("/", 1)
        shipped_name = shipped.rsplit("/", 1)[1]
        parent = stage / directory
        if not parent.is_dir():
            continue
        names = {entry.name for entry in parent.iterdir()}
        if staged_name in names and shipped_name in names:
            both.append((staged, shipped))
    return both


def _sources_with_bytecode(stage: pathlib.Path) -> set:
    """Every lib/**.py in *stage* that has compiled bytecode beside it."""
    found = set()
    for compiled in stage.rglob("*.pyc"):
        relative = str(compiled.relative_to(stage)).replace(os.sep, "/")
        parts = relative.split("/")
        if len(parts) < 2 or parts[-2] != "__pycache__":
            continue
        stem = parts[-1].split(".cpython")[0]
        found.add("/".join(parts[:-2] + [stem + ".py"]))
    return found


def _pe_imported_dlls(path: pathlib.Path) -> list:
    """The DLL names in *path*'s PE import table, or [] if it is not a PE file.

    A minimal reader: MZ header -> PE header -> optional header -> data
    directory 1 (imports) -> the name of each import descriptor, mapping RVAs
    through the section table. Enough to answer "what does this binary need
    loaded", which is all check_native_dependencies() asks.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return []
    if data[:2] != b"MZ":
        return []
    try:
        pe = struct.unpack_from("<I", data, 0x3C)[0]
        if data[pe:pe + 4] != b"PE\0\0":
            return []
        coff = pe + 4
        section_count = struct.unpack_from("<H", data, coff + 2)[0]
        optional_size = struct.unpack_from("<H", data, coff + 16)[0]
        optional = coff + 20
        pe32plus = struct.unpack_from("<H", data, optional)[0] == 0x20B
        directories = optional + (112 if pe32plus else 96)
        import_rva = struct.unpack_from("<I", data, directories + 8)[0]
        if not import_rva:
            return []

        sections = []
        table = optional + optional_size
        for index in range(section_count):
            entry = table + 40 * index
            virtual, raw_size, raw = struct.unpack_from("<III", data, entry + 12)
            sections.append((virtual, raw_size, raw))

        def offset_of(rva):
            for virtual, raw_size, raw in sections:
                if virtual <= rva < virtual + max(raw_size, 1):
                    return raw + (rva - virtual)
            return None

        names = []
        cursor = offset_of(import_rva)
        while cursor is not None:
            descriptor = data[cursor:cursor + 20]
            if len(descriptor) < 20 or descriptor == b"\0" * 20:
                break
            name_rva = struct.unpack_from("<I", descriptor, 12)[0]
            if not name_rva:
                break
            at = offset_of(name_rva)
            if at is None:
                break
            names.append(data[at:data.index(b"\0", at)].decode("ascii", "replace"))
            cursor += 20
        return names
    except (struct.error, ValueError):
        return []


def check_native_dependencies(payload: pathlib.Path, removed: set) -> None:
    """Refuse to ship a payload whose binaries need a file we took out.

    A trimmed or dropped DLL is invisible until someone runs hg.exe on
    Windows, and then it is fatal rather than degraded: the import that needs
    it is usually at module scope. This walks the .pyd/.dll/.exe files that
    survived, reads their PE import tables, and fails the build if any of them
    names a file this script removed and did not put back.

    Only names this script actually removed are considered, so the system DLLs
    every binary imports -- kernel32, the api-ms-win-crt-* set -- are ignored.
    """
    present = {p.name.lower() for p in payload.rglob("*") if p.is_file()}
    removed = {name.lower() for name in removed} - present

    broken = []
    for path in sorted(payload.rglob("*")):
        if path.suffix.lower() not in (".pyd", ".dll", ".exe"):
            continue
        for dll in _pe_imported_dlls(path):
            if dll.lower() in removed:
                broken.append((str(path.relative_to(payload)), dll))

    if broken:
        print("\nerror: the payload is missing native libraries it needs:",
              file=sys.stderr)
        for consumer, dll in broken:
            print("  %s imports %s, which was removed" % (consumer, dll),
                  file=sys.stderr)
        die("a trim rule removed a DLL that a surviving binary loads;"
            " re-run with --no-trim to confirm, then fix the rule")

    print("native dependencies: %d binaries checked, none left dangling"
          % sum(1 for p in payload.rglob("*")
                if p.suffix.lower() in (".pyd", ".dll", ".exe")))


def _lib_entry(relative: str) -> str | None:
    """The top-level name under lib/ that *relative* belongs to."""
    parts = relative.split("/")
    if len(parts) < 2 or parts[0] != "lib":
        return None
    return parts[1]


def _hgext_module(relative: str) -> str | None:
    """The hgext extension *relative* belongs to, however it is laid out.

    lib/hgext/eol.py, lib/hgext/__pycache__/eol.cpython-39.pyc and
    lib/hgext/git/gitutil.py all answer with the extension's own name.
    """
    parts = relative.split("/")
    if len(parts) < 3 or parts[:2] != ["lib", "hgext"]:
        return None
    name = parts[2]
    if name == "__pycache__":
        if len(parts) < 4:
            return None
        return parts[3].split(".cpython")[0]
    return name[:-3] if name.endswith(".py") else name


def is_trimmed(relative: str, trim_hgext: bool = True) -> str | None:
    """Why this payload path is being trimmed, or None to keep it."""
    # Decide a __pycache__ entry exactly as its module would be decided.
    # Without this, trimming a single-file module such as lib/six.py leaves
    # lib/__pycache__/six.cpython-39.pyc behind: the rules match on the
    # top-level name under lib/, which for that path is "__pycache__" and
    # matches nothing. Orphan bytecode is still importable, so the module is
    # not actually gone -- and lib/__pycache__ survives as a directory, which
    # costs a .guidsForInstaller.xml for the life of the product.
    parts = relative.split("/")
    if len(parts) >= 2 and parts[-2] == "__pycache__" and relative.endswith(".pyc"):
        stem = parts[-1].split(".cpython")[0]
        relative = "/".join(parts[:-2] + [stem + ".py"])

    if relative.split("/")[0] == "contrib":
        return "contrib/ (completions, editor and vim files, hgweb CGI)"

    entry = _lib_entry(relative)
    if entry is None:
        return None

    if entry.endswith(".dist-info") or entry.endswith(".egg-info"):
        return "installed-package metadata nothing reads"
    if entry in TRIM_UNIMPORTED_PACKAGES or entry in TRIM_UNIMPORTED_FILES:
        return "third-party code with no importer in the payload"
    if entry.startswith(TRIM_UNIMPORTED_PREFIXES):
        return "third-party code with no importer in the payload"
    if entry == "mercurial":
        parts = relative.split("/")
        if len(parts) > 2 and parts[2] in TRIM_DEAD_DATA:
            return "data Mercurial reads from the top level, not from lib/"

    if trim_hgext:
        if _hgext_module(relative) in TRIM_HGEXT:
            return "hgext extensions Chorus never enables"
        if entry in TRIM_HGEXT_PACKAGES or entry in TRIM_HGEXT_FILES:
            return "hgext extensions Chorus never enables"
        if entry.startswith(TRIM_HGEXT_PREFIXES):
            return "hgext extensions Chorus never enables"

    return None


# Files Mercurial's installers ship that create_pyoxidizer_install_layout()
# does not stage by itself, copied in afterwards so the staging tree matches
# the installers rather than just that one function.
#
#   * bash_completion and zsh_completion are in EXTRA_CONTRIB_FILES in
#     rust/hgcli/pyoxidizer.bzl, which builds the MSI, but are missing from
#     STAGING_RULES_WINDOWS in hgpackaging/pyoxidizer.py, which builds the
#     install layout. Both have shipped in this payload for years.
#   * defaultrc/mercurial.rc is added by hgpackaging/inno.py's
#     EXTRA_INSTALL_RULES and by the msi target in pyoxidizer.bzl. It is
#     entirely commented out, so it changes no behaviour; it is included to
#     keep the tree equal to the installers'.
# Two files this payload has carried since the TortoiseHg era are spelled in
# mixed case, and staging produces them in lower case:
#
#     defaultrc/mercurial.rc   <- EXTRA_STAGE_RULES, from contrib/win32/mercurial.ini
#     defaultrc/mergetools.rc  <- STAGING_RULES_APP, from lib/mercurial/defaultrc/*.rc
#
# Renaming them back is not cosmetic. MakeWixForDirTree derives each File Id
# from the name on disk, so shipping the lower-case spelling mints a second
# installer identity for a file that already has one. Both spellings are
# already in .guidsForInstaller.all.xml under different GUIDs, because a build
# before this map existed allocated the lower-case pair:
#
#     mercurial.defaultrc.Mercurial.rc    7dcf60f9-...   <- the one that ships
#     mercurial.defaultrc.mercurial.rc    78ec2c27-...   <- must stay unused
#     mercurial.defaultrc.MergeTools.rc   fa22312d-...   <- the one that ships
#     mercurial.defaultrc.mergetools.rc   9cbc7683-...   <- must stay unused
#
# Windows being case-insensitive, git records no rename when the build writes
# the lower-case name over the tracked mixed-case one, so the repository keeps
# the old spelling and nothing looks wrong -- until someone rebuilds on a
# case-sensitive filesystem, when both files quietly change installer identity.
# The predecessor of this script carried the same map for the same reason; it
# was dropped in the move to Mercurial on the mistaken view that lower-case
# staging was a TortoiseHg quirk. Mercurial's own staging does it too.
#
# Case-only entries. A genuine rename does not belong here: it would hide a
# real change of identity rather than preserve an existing one.
STAGE_RENAMES = {
    "defaultrc/mercurial.rc": "defaultrc/Mercurial.rc",
    "defaultrc/mergetools.rc": "defaultrc/MergeTools.rc",
}


EXTRA_STAGE_RULES = [
    ("contrib/bash_completion", "contrib/"),
    ("contrib/zsh_completion", "contrib/"),
    ("contrib/win32/mercurial.ini", "defaultrc/mercurial.rc"),
]


REGEN_PROJECT = pathlib.Path("assets") / "regen-guids.proj"


def run(command: list, cwd: pathlib.Path | None = None, what: str | None = None) -> None:
    printable = " ".join(str(part) for part in command)
    print("+ %s" % printable)
    result = subprocess.run([str(part) for part in command],
                            cwd=str(cwd) if cwd else None)
    if result.returncode != 0:
        die("%s failed with exit code %d" % (what or printable, result.returncode))


def _guid_files(payload: pathlib.Path) -> list:
    """Every GUID-recording file in *payload*, per-directory and consolidated."""
    return sorted(p for p in payload.rglob("*")
                  if p.is_file() and _is_guid_file(p.name))


def _guid_entries(payload: pathlib.Path) -> dict:
    """Every File Id recorded in the payload, mapped to the file recording it."""
    entries = {}
    for path in sorted(_guid_files(payload)):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for identifier in re.findall(r'Id="([^"]+)"', text):
            entries[identifier] = path
    return entries


def regenerate_guids(here: pathlib.Path, payload: pathlib.Path,
                     version: str = DEFAULT_SIL_BUILDTASKS_VERSION,
                     nuget_source: str | None = None) -> int:
    """Allocate MSI component GUIDs for any payload file lacking one.

    The GUIDs live in .guidsForInstaller.xml files inside the payload itself and
    pin per-file MSI component identities, so a file the new Mercurial adds
    needs one allocating and a file it drops keeps its old entry for continuity.
    SIL.BuildTasks' MakeWixForDirTree writes them into the tree it scans, so
    they are updated in place here.

    Any newly allocated id is listed afterwards, and is worth reading rather
    than skimming: an id that looks like an existing file under a different name
    is a rename, and has just been handed a second installer identity. The move
    from the TortoiseHg build to this one contains one by construction --
    contrib/hgk became contrib/hgk.tcl, which is the name Mercurial's own
    installers use -- so expect the first run after that move to allocate a very
    large number of ids and read the list for the ones that are not simply the
    new lib/ layout.

    assets/regen-guids.proj drives the task directly. Chorus has an equivalent
    MakeWixForDistFiles target, but reaching it means compiling ChorusHub and
    therefore LibChorus, a net462 WinForms build that the task does not need.
    See the comments in the .proj for what has to stay in step with Chorus.
    """
    project = here / REGEN_PROJECT
    if not project.is_file():
        die("%s is missing" % project)

    print("\nregenerating installer GUIDs (SIL.BuildTasks %s)" % version)
    before_entries = _guid_entries(payload)
    before_bytes = {path: path.read_bytes() for path in _guid_files(payload)}

    # -restore in the same invocation: the task assembly arrives via
    # PackageReference, and its UsingTask via the package's own props.
    command = ["dotnet", "msbuild", project, "-restore", "-nologo",
               "-t:RegenerateGuids",
               "-p:PayloadDir=%s" % payload,
               "-p:SilBuildTasksVersion=%s" % version]
    if nuget_source:
        command.append("-p:RestoreAdditionalProjectSources=%s"
                       % pathlib.Path(nuget_source).resolve())
    run(command, cwd=here, what="dotnet msbuild -t:RegenerateGuids")

    consolidated = payload / CONSOLIDATED_GUID_FILE
    if not consolidated.is_file():
        die("%s was not written; does SIL.BuildTasks %s have"
            " ConsolidatedGuidFile?" % (consolidated, version))

    after = _guid_files(payload)
    if not after:
        die("no %s files under %s; did the task actually run?"
            % (GUID_FILE, payload))

    added = sorted(set(_guid_entries(payload)) - set(before_entries))
    if added:
        print("  %d new GUID(s) allocated:" % len(added))
        for identifier in added:
            print("    + %s" % identifier)
        print("  Check that each is a genuinely new file. One that looks like an"
              " existing\n  file under another name is a rename, and has just been"
              " given a second\n  installer identity.")
    else:
        print("  no new GUIDs were needed")

    changed = 0
    for path in after:
        was = before_bytes.get(path)
        if was is None or was != path.read_bytes():
            print("  %s %s" % ("added  " if was is None else "updated", path))
            changed += 1
    if changed:
        print("  %d of %d GUID file(s) changed" % (changed, len(after)))

    total = len(_guid_entries(payload))
    in_consolidated = len(re.findall(
        r'Id="([^"]+)"', consolidated.read_text(encoding="utf-8-sig",
                                                errors="replace")))
    print("  %s holds %d of the %d id(s) in the payload"
          % (CONSOLIDATED_GUID_FILE, in_consolidated, total))
    if in_consolidated < total:
        print("  warning: some ids are only in the per-directory files; the"
              " consolidated\n           file is not yet a complete"
              " replacement for them")
    return changed


def check_guids(here: pathlib.Path, payload: pathlib.Path,
                version: str = DEFAULT_SIL_BUILDTASKS_VERSION,
                nuget_source: str | None = None) -> None:
    """Verify the GUID files describe the payload, without writing anything.

    Runs MakeWixForDirTree again with CheckOnly, which allocates nothing and
    instead errors out naming what it would have had to allocate. Run straight
    after regenerate_guids(), where it is a post-condition rather than a
    question: everything has just been allocated, so a complaint here means
    something is wrong rather than something is new.

    Three things it catches that the regeneration itself does not report:

      * a file with no GUID anywhere, which after a successful regeneration
        means the task did not write what it said it wrote;
      * a GUID still held only in a per-directory .guidsForInstaller.xml and
        not in the consolidated file, which is what says whether the
        per-directory files can finally be deleted;
      * a per-directory file and the consolidated file disagreeing about one
        id, which is two GUIDs claiming one installer component.

    The check is skipped along with the regeneration under --no-regen-guids,
    since it needs the same .NET SDK and the same restored package.
    """
    project = here / REGEN_PROJECT
    print("\nverifying the GUID files describe the payload")

    command = ["dotnet", "msbuild", project, "-nologo", "-t:CheckGuids",
               "-p:PayloadDir=%s" % payload,
               "-p:SilBuildTasksVersion=%s" % version]
    if nuget_source:
        command.append("-p:RestoreAdditionalProjectSources=%s"
                       % pathlib.Path(nuget_source).resolve())

    printable = " ".join(str(part) for part in command)
    print("+ %s" % printable)
    result = subprocess.run([str(part) for part in command], cwd=str(here))
    if result.returncode != 0:
        die("the payload and its GUID files disagree; see the errors above."
            "\n       %s has been written but must not be committed until this"
            "\n       passes -- an id allocated now and lost later is an"
            " installer component\n       that changes identity on the next"
            " build." % payload)
    print("  every file in the payload has a GUID, and the consolidated file"
          " holds them all")


def hg_command(repo: pathlib.Path, *args: str) -> str:
    """Run hg in *repo* and return its output."""
    result = subprocess.run(["hg", "--repository", str(repo), *args],
                            capture_output=True, text=True)
    if result.returncode != 0:
        die("hg %s in %s failed: %s" % (" ".join(args), repo, result.stderr.strip()))
    return result.stdout.strip()


def build_staging_tree(hg: pathlib.Path, tag: str | None, target_triple: str,
                       stage: pathlib.Path) -> pathlib.Path:
    """Build Mercurial and return the staging tree its packaging produces.

    This drives Mercurial's own contrib/packaging code rather than
    reimplementing it, so the result is by construction the install layout its
    Inno Setup and WiX installers are built from.
    """
    packaging = hg / "contrib" / "packaging"
    if not (packaging / "hgpackaging" / "pyoxidizer.py").is_file():
        die("%s does not look like a Mercurial checkout (no hgpackaging)" % hg)

    if tag:
        print("updating %s to %s" % (hg, tag))
        hg_command(hg, "update", tag)

    tags = hg_command(hg, "identify", "--tags").split()
    print("building Mercurial %s for %s" % (" ".join(tags) or "(untagged)",
                                            target_triple))
    if DEFAULT_HG_TAG not in tags:
        print("warning: this checkout is not at %s, which is the tag this"
              " payload is meant\n         to be built from; pass --tag %s to"
              " update it" % (DEFAULT_HG_TAG, DEFAULT_HG_TAG))

    sys.path.insert(0, str(packaging))
    from hgpackaging import pyoxidizer as hgpyoxidizer
    from hgpackaging.util import process_install_rules

    # Mirrors hgpackaging/inno.py: the build_dir passed here is only used to
    # cache the gettext download, since run_pyoxidizer always writes its own
    # artifacts under <hg>/build/pyoxidizer/.
    build_dir = hg / "build" / ("payload-pyoxidizer-%s" % target_triple)
    build_dir.mkdir(parents=True, exist_ok=True)

    # create_pyoxidizer_install_layout() purges its output directory itself.
    with documentation_build_skipped(hgpyoxidizer):
        hgpyoxidizer.create_pyoxidizer_install_layout(
            hg, build_dir, stage, target_triple
        )

    process_install_rules(EXTRA_STAGE_RULES, hg, stage)

    return stage


@contextlib.contextmanager
def documentation_build_skipped(module):
    """Neuter the HTML documentation step in the layout build.

    create_pyoxidizer_install_layout() calls build_docs_html(), which shells
    out to `setup.py build_doc --html` and so needs docutils importable by
    whichever interpreter is running this script. Upstream gets that from the
    bootstrap venv contrib/packaging/packaging.py creates from requirements.txt;
    we call into hgpackaging directly and so never have it.

    Nothing downstream misses it: the only staging rule that consumes the
    result is a doc/*.html glob, which simply matches nothing, and doc/ is
    dropped. doc/style.css is a rule of its own and is staged either way, into
    the same dropped directory.
    """
    original = module.build_docs_html

    def skipped(source_dir):
        print("  skipping Mercurial's HTML documentation; doc/ is not shipped")

    module.build_docs_html = skipped
    try:
        yield
    finally:
        module.build_docs_html = original


def assemble_payload(stage: pathlib.Path, payload: pathlib.Path,
                     trim: bool = True, trim_hgext: bool = True,
                     trim_sources: bool = True
                     ) -> tuple[list[str], list[str], int, dict, set]:
    """Replace *payload* with the wanted part of *stage*, keeping our own files."""
    def files_in(root: pathlib.Path) -> set[str]:
        if not root.exists():
            return set()
        return {str(p.relative_to(root)).replace(os.sep, "/")
                for p in root.rglob("*") if p.is_file()}

    before = files_in(payload)

    # Every GUID file in the payload, wherever it sits, is carried across and
    # restored afterwards. A GUID has to stay attached to its path for the life
    # of the product, and an entry has to outlive the file it describes so the
    # installer can still remove it.
    guids = {relative: (payload / relative).read_bytes()
             for relative in sorted(before)
             if _is_guid_file(relative.rsplit("/", 1)[-1])}

    with tempfile.TemporaryDirectory() as tmp:
        kept = pathlib.Path(tmp)
        for relative in PRESERVE:
            source = payload / relative
            if not source.is_file():
                print("note: %s not present, nothing to preserve" % relative)
                continue
            destination = kept / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

        if payload.exists():
            shutil.rmtree(payload)
        payload.mkdir(parents=True)

        # Two real entries can only exist on a case-sensitive staging tree, and
        # one would silently overwrite the other on the way into the payload.
        for staged, shipped in _staged_both_spellings(stage):
            die("the staging tree holds both %s and %s as separate files;"
                " STAGE_RENAMES\n       cannot tell which one the payload should"
                " carry. Delete whichever is stale." % (staged, shipped))

        dropped = 0
        trimmed: dict = {}
        removed_names: set = set()
        compiled = _sources_with_bytecode(stage) if trim_sources else set()
        uncompiled = 0
        renamed = 0
        for source in sorted(stage.rglob("*")):
            if not source.is_file():
                continue
            relative = str(source.relative_to(stage)).replace(os.sep, "/")
            if is_dropped(relative):
                dropped += 1
                removed_names.add(source.name)
                continue
            if trim:
                reason = is_trimmed(relative, trim_hgext)
                if reason is not None:
                    entry = trimmed.setdefault(reason, [0, 0])
                    entry[0] += 1
                    entry[1] += source.stat().st_size
                    removed_names.add(source.name)
                    continue
            if (trim_sources and relative.startswith("lib/")
                    and relative.endswith(".py")):
                if relative in compiled:
                    entry = trimmed.setdefault(
                        "Python source (hg.exe loads the bytecode)", [0, 0])
                    entry[0] += 1
                    entry[1] += source.stat().st_size
                    removed_names.add(source.name)
                    continue
                uncompiled += 1
            # A staging tree that already carries the shipped spelling -- an
            # unpacked MSI, say -- simply misses the lookup.
            if relative in STAGE_RENAMES:
                relative = STAGE_RENAMES[relative]
                renamed += 1
            destination = payload / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

        if renamed:
            print("renamed %d staged file(s) to the spelling the payload has"
                  " always used" % renamed)

        if uncompiled:
            print("note: kept %d .py file(s) that have no bytecode beside them;"
                  " removing them\n      would make those modules unimportable"
                  % uncompiled)

        for relative in PRESERVE:
            source = kept / relative
            if source.is_file():
                destination = payload / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)

    for relative, content in guids.items():
        destination = payload / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)
    if guids:
        consolidated = sum(1 for r in guids
                           if r.rsplit("/", 1)[-1] == CONSOLIDATED_GUID_FILE)
        print("guids: carried %d file(s) across (%d per-directory, %d consolidated)"
              % (len(guids), len(guids) - consolidated, consolidated))

    after = files_in(payload)
    return (sorted(after - before), sorted(before - after), dropped, trimmed,
            removed_names)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hg-source",
        help="Mercurial checkout to build (default: ../hg beside this repository)",
    )
    parser.add_argument(
        "--tag",
        help="update the Mercurial checkout to this tag before building"
             " (this payload is meant to be %s)" % DEFAULT_HG_TAG,
    )
    parser.add_argument(
        "--target-triple", default=DEFAULT_TARGET_TRIPLE, choices=TARGET_TRIPLES,
        help="PyOxidizer target to build for (default: %(default)s)",
    )
    parser.add_argument(
        "--from-stage", metavar="DIR",
        help="skip the build and take the payload from an existing staging tree,"
             " such as a Mercurial MSI unpacked with `msiexec /a`",
    )
    parser.add_argument(
        "--output",
        help="payload directory to refresh (default: win/Mercurial beside this script)",
    )
    parser.add_argument(
        "--no-trim", action="store_true",
        help="ship everything the install layout contains, turning off all"
             " three trimming passes at once. Use it to reproduce the untrimmed"
             " tree when working out whether a problem is the trimming's fault",
    )
    parser.add_argument(
        "--no-trim-hgext", action="store_true",
        help="keep the hgext extensions Chorus never enables, and the packages"
             " only they import (pygments, pygit2). They are trimmed by default;"
             " restore them if something outside this package enables one of"
             " those extensions",
    )
    parser.add_argument(
        "--no-trim-sources", action="store_true",
        help="keep the .py files under lib/. They are dropped by default,"
             " because hg.exe loads the bytecode beside them; restore them when"
             " a Mercurial traceback needs to show the source line it failed"
             " on rather than just the file, line number and function",
    )
    parser.add_argument(
        "--sil-buildtasks-version", default=DEFAULT_SIL_BUILDTASKS_VERSION,
        help="SIL.BuildTasks version to allocate GUIDs with (default:"
             " %(default)s, the first release with ConsolidatedGuidFile)",
    )
    parser.add_argument(
        "--nuget-source", metavar="DIR",
        help="additional NuGet source to restore SIL.BuildTasks from, for"
             " trying a build of it that is not on nuget.org",
    )
    parser.add_argument(
        "--no-regen-guids", action="store_true",
        help="do not reallocate MSI component GUIDs; skips the only step that"
             " needs the .NET SDK",
    )
    args = parser.parse_args()

    here = pathlib.Path(__file__).resolve().parent
    payload = (pathlib.Path(args.output).resolve() if args.output
               else here / "win" / "Mercurial")

    if args.from_stage:
        stage = pathlib.Path(args.from_stage).resolve()
        if not stage.is_dir():
            die("no such staging tree: %s" % stage)
        print("using the staging tree at %s" % stage)
    else:
        if os.name != "nt":
            die("building Mercurial for Windows needs Windows; use --from-stage"
                " to assemble a payload from a tree built elsewhere")
        hg = (pathlib.Path(args.hg_source).resolve() if args.hg_source
              else here.parent / "hg")
        if not hg.is_dir():
            die("no Mercurial checkout at %s (pass --hg-source)" % hg)
        stage = build_staging_tree(hg, args.tag, args.target_triple,
                                   here / "build" / "stage")

    if not (stage / "hg.exe").is_file():
        die("no hg.exe in %s; is that really a Mercurial install layout?" % stage)

    # --no-trim turns everything off, so that it still means "the install
    # layout exactly as staged"; the other two switch off one pass each.
    trim = not args.no_trim
    trim_hgext = trim and not args.no_trim_hgext
    trim_sources = trim and not args.no_trim_sources

    added, removed, dropped, trimmed, removed_names = assemble_payload(
        stage, payload, trim=trim, trim_hgext=trim_hgext,
        trim_sources=trim_sources)

    check_native_dependencies(payload, removed_names)

    if trimmed:
        total_files = sum(n for n, _ in trimmed.values())
        total_bytes = sum(b for _, b in trimmed.values())
        print("\ntrimmed %d file(s), %.1f MB, that Chorus cannot reach:"
              % (total_files, total_bytes / 1e6))
        for reason, (count, size) in sorted(trimmed.items(),
                                            key=lambda kv: -kv[1][1]):
            print("  %6.1f MB  %4d file(s)  %s" % (size / 1e6, count, reason))
        if not trim_hgext:
            print("  (--no-trim-hgext: the unused hgext extensions were kept)")
        if not trim_sources:
            print("  (--no-trim-sources: the .py sources were kept)")
    elif args.no_trim:
        print("\ntrimming disabled: shipping the install layout as staged")

    regenerated = not args.no_regen_guids
    if regenerated:
        regenerate_guids(here, payload, args.sil_buildtasks_version,
                         args.nuget_source)
        check_guids(here, payload, args.sil_buildtasks_version,
                    args.nuget_source)

    total = sum(1 for p in payload.rglob("*") if p.is_file())
    directories = len({p.parent for p in payload.rglob("*") if p.is_file()})
    print("\n%s rebuilt: %d file(s) in %d director(ies), %d left out of the"
          " staging tree" % (payload, total, directories, dropped))
    print("  %d added, %d removed relative to what was there before"
          % (len(added), len(removed)))
    for name in added:
        print("    + %s" % name)
    for name in removed:
        print("    - %s" % name)

    print(
        "\nNext steps:\n"
        "  1. Sanity-check the payload:  %s\\hg.exe version\n"
        "  2. Expect hg.exe and everything under lib/ to differ byte-for-byte\n"
        "     from the last build even at the same tag; the file list should not.\n"
        "  3. Set MercurialVersion in SIL.Chorus.Mercurial.csproj, update the\n"
        "     mercurial-version matrix in .github/workflows/nuget-ci-cd.yml, add a\n"
        "     PackageReleaseNotes entry, and set DEFAULT_HG_TAG in this script.\n"
        "  4. %s"
        % (payload,
           "Commit the refreshed .guidsForInstaller.xml files with this payload."
           if regenerated else
           "GUID regeneration was skipped; re-run without --no-regen-guids"
           " before committing this payload.")
    )


if __name__ == "__main__":
    main()