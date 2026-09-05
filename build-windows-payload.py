#!/usr/bin/env python3
"""Rebuild win/Mercurial by building Mercurial from source.

MUST BE RUN ON WINDOWS. Needs the following prerequisites:

- Python 3 (run `py` to see if you have it)
- Rust, specifically Rustup (`winget install Rustlang.Rustup`)
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
    py -3 build-windows-payload.py --no-trim         # useful for debugging a build
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import os
import pathlib
import re
import shutil
import struct
import subprocess
import sys
import tempfile

# The Mercurial tag this script expects to see in `../hg`. Will be overridden
# by `--tag`, but if the Mercurial source tree is NOT at this version, script
# will warn you that you might be building a different version than you think.
#
# Keep this in sync with MercurialVersion in SIL.Chorus.Mercurial.csproj and
# the mercurial-version matrix in .github/workflows/nuget-ci-cd.yml.
DEFAULT_HG_TAG = "7.0.1"

# Target triples for PyOxidizer; you usually won't need to change this.
DEFAULT_TARGET_TRIPLE = "x86_64-pc-windows-msvc"
TARGET_TRIPLES = ["i686-pc-windows-msvc", "x86_64-pc-windows-msvc"]

# Files not to delete when cleaning out the old win/Mercurial build
PRESERVE = [
    "mercurial.ini",
    "Mercurial.url",
    "cacert.pem",
    # hg never reads this file: it has read defaultrc as a package resource
    # since 5.3, and default.d not since 5.2.2. It is kept only because
    # Chorus's ChorusMergeModule.wxs names it by hand, so dropping it would
    # break that build. Drop it once Chorus stops naming it.
    "default.d/cacerts.rc",
]

# Two more files to preserve wherever they are found (as opposed to the PRESERVE
# list which specifies full paths relative to the root of the win/Mercurial tree)
# These are used by SIL.BuildTasks to build WiX installers, and must never be
# automatically deleted (though the individual .guidsForInstaller.xml files will
# be able to be deleted once Chorus has switched over to SIL.BuildTasks 3.3.0)
GUID_FILE = ".guidsForInstaller.xml"
CONSOLIDATED_GUID_FILE = ".guidsForInstaller.all.xml"

# First SIL.BuildTasks with MakeWixForDirTree.ConsolidatedGuidFile. An older
# one restores fine and then fails with MSB4064 once the task runs. Override
# with --sil-buildtasks-version.
DEFAULT_SIL_BUILDTASKS_VERSION = "3.3.0"


def _is_guid_file(name: str) -> bool:
    """Is *name* one of the files recording installer GUIDs?"""
    return name == GUID_FILE or name == CONSOLIDATED_GUID_FILE


# Where die(payload_wiped=True) tells you to restore from, relative to the
# repository. main() sets it once --output is known; None means --output put
# the payload somewhere git cannot reach.
PAYLOAD_PATH: str | None = "win/Mercurial"


def die(msg: str, payload_wiped: bool = False) -> None:
    """Print *msg* and stop.

    Pass *payload_wiped* from anything that fails after assemble_payload() has
    emptied the payload directory. What is on disk is then a half-finished
    build, and the last thing printed should be how to get the committed one
    back -- `restore` alone would leave behind any file the new payload added.
    """
    print("error: %s" % msg, file=sys.stderr)
    if payload_wiped and PAYLOAD_PATH:
        print("\n%s has already been replaced. Put the committed one back with:"
              "\n  git restore %s && git clean -fdq %s"
              % (PAYLOAD_PATH, PAYLOAD_PATH, PAYLOAD_PATH), file=sys.stderr)
    elif payload_wiped:
        print("\nthe payload has already been replaced, and --output put it"
              " outside this\nrepository, so git cannot put it back.",
              file=sys.stderr)
    raise SystemExit(1)


# What hgpackaging stages that this payload does not want. These are written
# as exclusions, not an allowlist, so that a file a later Mercurial adds gets
# shipped by default. Watch the added/removed summary to catch anything that
# slips in that way.

# Only useful in a standalone Mercurial install: ReadMe.html indexes the doc/
# tree we drop, and ReleaseNotes.txt is the installer's post-install notes.
# Copying.txt stays -- it is the licence, and it already has a GUID.
DROP_ROOT_FILES = [
    "ReadMe.html",
    "ReleaseNotes.txt",
]

# Whole directories, none of which this payload has ever shipped. Each is a
# top-level copy of data that also lives under lib/, plus doc/, which is not
# built at all. Only the top-level copies go: hg reads help text and
# configitems.toml from lib/ through importlib, so those still work. Dropping
# templates/ means `hg log --style=X` does not, which Chorus never uses.
DROP_DIRECTORIES = [
    "defaultrc",
    "doc",
    "helptext",
    "locale",
    "templates",
]

# Exact paths to leave out. Empty, and worth keeping empty: everything else
# hgpackaging stages is a file Mercurial's own installers ship.
DROP_FILES = []

# The rules above drop nothing under lib/. That is hg.exe's own module tree,
# indexed by concrete path inside the executable, and pruning it is how a
# frozen application breaks. The trimming below is the narrower exception.


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
# Everything below is a file Mercurial's installers ship that Chorus cannot
# reach. It is kept apart from DROP_* because the question is different: those
# rules are about the shape of the install layout, these are about what Chorus
# uses.
#
# Sizes below were measured over the official 7.0.1 x64 MSI, unpacked with
# `msiexec /a`, which is the same install layout this script stages.
# The nupkg column is a real dotnet pack of this repository, so it carries the
# committed linux-x64 tree; the published package is larger.
#
#     mode                                 files  dirs   raw     nupkg
#     --no-trim                             3272   347   99.9 MB  37.9 MB
#     --no-trim-hgext --no-trim-sources     1515    99   75.9 MB  29.0 MB
#     --no-trim-hgext                        832    65   63.3 MB  25.5 MB
#     --no-trim-sources                      658    58   60.1 MB  23.8 MB
#     (default)                              384    40   54.0 MB  22.1 MB
#     TortoiseHg                              99     6   46.3 MB  23.4 MB  <- replaced
#
# Watch the directory count as much as the megabytes. Every directory needs a
# GUID entry kept for the life of the product, and one created can never be
# cleanly retired.
#
# All three passes are on by default. To qualify for TRIM_UNIMPORTED_*, which
# has no flag of its own, nothing in the shipped mercurial/ and hgext/ trees
# may import it, or the only imports are inside a try/except. Anything with a
# real importer goes in TRIM_HGEXT instead, which --no-trim-hgext restores.
# Check with:
#     grep -rl "import <name>" lib/mercurial lib/hgext --include=*.py

# Top-level entries under lib/ that nothing in the payload imports. On Windows
# pyoxidizer.bzl pip-installs all of requirements-windows-py3.txt "for
# convenience", which is mostly Mercurial's own test and release tooling.
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

# Matched on the start of the name, because the ABI tag in a .pyd filename
# moves with the interpreter version.
#
# _curses is the exception to "no importer": color.py, crecord.py and
# histedit.py all import it inside a try/except that falls back cleanly.
# crecord is the `hg commit -i` UI, which Chorus never invokes.
TRIM_UNIMPORTED_PREFIXES = ("_curses",)

# Data under lib/mercurial/ that hg does not read from there. It reads locale
# and templates from beside hg.exe, and DROP_DIRECTORIES already removes those
# copies, so these are dead weight twice over.
#
# helptext is deliberately not here: hg does read that one from lib/, so
# trimming it would break `hg help <topic>`.
TRIM_DEAD_DATA = {"locale", "templates"}

# hgext extensions Chorus never enables, plus the packages only they import.
# Trimmed by default; --no-trim-hgext restores them.
#
# Unlike the lists above these are live code paths, so this was opt-in until
# the full LibChorus suite passed on Windows against a payload built this way.
# What is still reachable is a config file outside this package enabling one of
# them, which is what the flag is for.
#
# Chorus enables eol, hgext.graphlog and convert in the payload's mercurial.ini,
# plus the vendored fixutf8. Those are kept, as is anything not named here --
# this is a denylist, so an extension a later Mercurial adds ships by default.
TRIM_HGEXT = {
    "absorb", "acl", "amend", "automv", "beautifygraph", "blackbox",
    "bookflow", "bugzilla", "censor", "children", "churn", "clonebundles",
    "closehead", "commitextras", "extdiff", "factotum", "fastannotate",
    "fastexport", "fetch", "fix", "fsmonitor", "git", "githelp", "gpg", "hgk",
    "highlight", "histedit", "hooklib", "journal", "keyword", "largefiles",
    "lfs", "logtoprocess", "mq", "narrow", "notify", "pager", "patchbomb",
    "phabricator", "purge", "rebase", "record", "relink", "releasenotes",
    "remotefilelog", "remotenames", "schemes", "share", "show", "sparse",
    "split", "sqlitestore", "strip", "transplant", "uncommit", "win32mbcs",
    "win32text", "zeroconf",
}

# Reached only from an extension in TRIM_HGEXT, so they go with it: highlight
# is the only importer of pygments, git the only importer of pygit2, and cffi
# and pycparser are pygit2's dependencies.
#
# Never add a DLL here on the strength of its name. libffi-7.dll was trimmed
# with cffi once, and it belongs to lib/_ctypes.pyd; mercurial/win32.py imports
# ctypes at module scope, so the payload could not run a single command.
# check_native_dependencies() catches that now.
TRIM_HGEXT_PACKAGES = {"pygments", "pygit2", "cffi", "pycparser"}
TRIM_HGEXT_PREFIXES = ("_cffi_backend",)


# Python source, dropped by default and restored by --no-trim-sources.
#
# hg.exe does not need it: its resource index records the source and the
# bytecode as separate paths, and the bytecode is PEP 552 unchecked-hash, so
# nothing ever validates one against the other.
#
# What you lose is the source line in a traceback. A crash still names the
# file, line and function, but the line itself comes out blank. Use
# --no-trim-sources when a crash needs investigating.
#
# A .py is only removed when its bytecode is actually present, so this cannot
# make a module unimportable if a later Mercurial ships something PyOxidizer
# does not compile.


# Kept modules that do import a removed package at module scope, and may.
# Neither is reachable in the payload: mercurial/cffi's *build.py are the cffi
# code generators setup.py runs at build time, and pygments' sphinxext is a
# Sphinx plugin. Add to this only after establishing that nothing hg runs can
# import the module -- the point of the check is that the answer is usually no.
IMPORT_ALLOWED = {
    "lib/mercurial/cffi/bdiffbuild.py",
    "lib/mercurial/cffi/mpatchbuild.py",
    "lib/mercurial/cffi/osutilbuild.py",
    "lib/pygments/sphinxext.py",
}


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

    A missing DLL stays invisible until someone runs hg.exe on Windows, and
    then it is fatal rather than degraded, because the import that needs it is
    usually at module scope. This reads the PE import table of every surviving
    .pyd/.dll/.exe and fails the build if one needs a file we removed.

    Only names this script removed count, so the system DLLs every binary
    imports are ignored.

    A name is looked for beside the binary that needs it and in the payload
    root, which is where Windows resolves it from. Asking whether it exists
    anywhere in the tree would let an unrelated file answer for it.
    """
    removed = {name.lower() for name in removed}
    beside_exe = {p.name.lower() for p in payload.iterdir() if p.is_file()}

    broken = []
    for path in sorted(payload.rglob("*")):
        if path.suffix.lower() not in (".pyd", ".dll", ".exe"):
            continue
        alongside = {p.name.lower() for p in path.parent.iterdir() if p.is_file()}
        for dll in _pe_imported_dlls(path):
            name = dll.lower()
            if name in removed and name not in alongside | beside_exe:
                broken.append((str(path.relative_to(payload)), dll))

    if broken:
        print("\nerror: the payload is missing native libraries it needs:",
              file=sys.stderr)
        for consumer, dll in broken:
            print("  %s imports %s, which was removed" % (consumer, dll),
                  file=sys.stderr)
        die("a trim rule removed a DLL a surviving binary loads.",
            payload_wiped=True)

    print("native deps: %d binaries, none dangling"
          % sum(1 for p in payload.rglob("*")
                if p.suffix.lower() in (".pyd", ".dll", ".exe")))


# Compound statements _runs_on_import() does not enter. A try is where an
# optional import belongs, and a function body waits to be called. A class body
# does run at definition, but an import there is deliberately not treated as a
# payload dependency. TryStar is except*, which only exists from 3.11.
_NOT_ON_IMPORT = tuple(
    node for node in (getattr(ast, name, None) for name in
                      ("Try", "TryStar", "FunctionDef", "AsyncFunctionDef",
                       "ClassDef"))
    if node is not None)

# match, which only exists from 3.10.
_MATCH = getattr(ast, "Match", None)


def _is_typechecking(test) -> bool:
    """Is *test* the `if TYPE_CHECKING:` guard, in any of its spellings?"""
    return any((isinstance(node, ast.Name) and node.id == "TYPE_CHECKING")
               or (isinstance(node, ast.Attribute) and node.attr == "TYPE_CHECKING")
               for node in ast.walk(test))


def _runs_on_import(body: list) -> list:
    """The statements in *body* that run when the module is imported.

    Descends into every compound statement whose body runs on the way past --
    `if`, `for`, `while`, `with`, `match`, and the `else` of the ones that have
    one -- but not into the statements in _NOT_ON_IMPORT.

    `if TYPE_CHECKING:` does not run either: Mercurial uses it to point pytype
    at the non-vendored attrs, and taking those blocks at face value flags two
    dozen modules for a package that is correctly trimmed. Its `else` does run,
    so that branch is still followed.
    """
    runs = []
    for node in body:
        if isinstance(node, _NOT_ON_IMPORT):
            continue
        if isinstance(node, ast.If):
            if not _is_typechecking(node.test):
                runs += _runs_on_import(node.body)
            runs += _runs_on_import(node.orelse)
        elif isinstance(node, (ast.For, ast.AsyncFor, ast.While)):
            runs += _runs_on_import(node.body)
            runs += _runs_on_import(node.orelse)
        elif isinstance(node, (ast.With, ast.AsyncWith)):
            runs += _runs_on_import(node.body)
        elif _MATCH is not None and isinstance(node, _MATCH):
            for case in node.cases:
                runs += _runs_on_import(case.body)
        else:
            runs.append(node)
    return runs


def _imported_on_import(source: pathlib.Path) -> set:
    """The top-level package names *source* imports as it is imported."""
    try:
        tree = ast.parse(source.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return set()
    names = set()
    for node in _runs_on_import(tree.body):
        if isinstance(node, ast.Import):
            names |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def check_python_imports(stage: pathlib.Path, trim: bool, trim_hgext: bool) -> None:
    """Refuse to ship a payload whose own modules import something we removed.

    check_native_dependencies() reads PE import tables and so sees none of
    this. A trim rule can take out a package that a surviving module imports at
    module scope, and nothing says so until hg raises ImportError on a user's
    machine -- the same failure as libffi-7.dll, on the other side of the
    language boundary.

    Only a top-level name under lib/ that the rules remove *entirely* counts,
    and only an import that runs when the module is imported. Both halves are
    re-derived from the same rules here rather than passed in, so this asks its
    question of the rules as they stand rather than of one run's bookkeeping.
    """
    seen: dict = {}
    gone: dict = {}
    kept = []
    for source in sorted(stage.rglob("*")):
        if not source.is_file():
            continue
        relative = str(source.relative_to(stage)).replace(os.sep, "/")
        removed = is_dropped(relative) or (
            trim and is_trimmed(relative, trim_hgext) is not None)
        entry = _lib_entry(relative)
        if entry:
            seen[entry] = seen.get(entry, 0) + 1
            if removed:
                gone[entry] = gone.get(entry, 0) + 1
        if not removed and relative.endswith(".py") and relative.startswith("lib/"):
            kept.append((relative, source))

    dropped_whole = {entry for entry in seen if gone.get(entry, 0) == seen[entry]}
    broken = []
    for relative, source in kept:
        if relative in IMPORT_ALLOWED:
            continue
        for name in sorted(_imported_on_import(source) & dropped_whole):
            broken.append((relative, name))

    if broken:
        print("\nerror: the payload keeps modules that import what it removed:",
              file=sys.stderr)
        for module, name in broken:
            print("  %s imports %s, which was removed" % (module, name),
                  file=sys.stderr)
        die("a trim rule removed a package a surviving module needs. If the"
            " module is\n       reachable only from tooling this payload does"
            " not ship, add it to\n       IMPORT_ALLOWED and say why.",
            payload_wiped=True)

    print("python imports: %d modules, none reaching a removed package" % len(kept))


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
    # Decide a __pycache__ entry the same way as its module. Without this,
    # trimming lib/six.py leaves lib/__pycache__/six.cpython-39.pyc behind,
    # because the rules match on the top-level name under lib/, which for that
    # path is "__pycache__". Orphan bytecode is still importable, so the module
    # would not really be gone, and the directory would survive with it.
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
        if entry in TRIM_HGEXT_PACKAGES:
            return "hgext extensions Chorus never enables"
        if entry.startswith(TRIM_HGEXT_PREFIXES):
            return "hgext extensions Chorus never enables"

    return None


REGEN_PROJECT = pathlib.Path("assets") / "regen-guids.proj"


def run(command: list, cwd: pathlib.Path | None = None, what: str | None = None,
        payload_wiped: bool = False) -> None:
    printable = " ".join(str(part) for part in command)
    print("+ %s" % printable)
    result = subprocess.run([str(part) for part in command],
                            cwd=str(cwd) if cwd else None)
    if result.returncode != 0:
        die("%s failed with exit code %d" % (what or printable, result.returncode),
            payload_wiped=payload_wiped)


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

    The GUIDs pin per-file MSI component identities, so a file a new Mercurial
    adds needs one allocated, and a file it drops keeps its old entry.
    MakeWixForDirTree writes them into the tree it scans, so they are updated
    in place.

    Read the list of new ids it prints rather than skimming it: an id that
    looks like an existing file under a different name is a rename that has
    just been given a second installer identity.

    assets/regen-guids.proj drives the task directly, rather than going through
    Chorus's equivalent MakeWixForDistFiles target, which would mean compiling
    ChorusHub and LibChorus for a task that only walks a directory.
    """
    project = here / REGEN_PROJECT
    if not project.is_file():
        die("%s is missing" % project, payload_wiped=True)

    print("\nGUIDs (SIL.BuildTasks %s)" % version)
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
    run(command, cwd=here, what="dotnet msbuild -t:RegenerateGuids",
        payload_wiped=True)

    consolidated = payload / CONSOLIDATED_GUID_FILE
    if not consolidated.is_file():
        die("%s was not written; does SIL.BuildTasks %s have"
            " ConsolidatedGuidFile?" % (consolidated, version), payload_wiped=True)

    after = _guid_files(payload)
    if not after:
        die("no %s files under %s; did the task actually run?"
            % (GUID_FILE, payload), payload_wiped=True)

    added = sorted(set(_guid_entries(payload)) - set(before_entries))
    if added:
        print("  %d new id(s) -- check for renames, which get a second"
              " installer identity:" % len(added))
        for identifier in added:
            print("    + %s" % identifier)
    else:
        print("  no new ids")

    changed = 0
    for path in after:
        was = before_bytes.get(path)
        if was is None or was != path.read_bytes():
            print("  %s %s" % ("added  " if was is None else "updated", path))
            changed += 1
    if changed:
        print("  %d of %d file(s) changed" % (changed, len(after)))

    total = len(_guid_entries(payload))
    in_consolidated = len(re.findall(
        r'Id="([^"]+)"', consolidated.read_text(encoding="utf-8-sig",
                                                errors="replace")))
    print("  %s: %d of %d id(s)"
          % (CONSOLIDATED_GUID_FILE, in_consolidated, total))
    if in_consolidated < total:
        print("  warning: the rest are only in the per-directory files")
    return changed


def check_guids(here: pathlib.Path, payload: pathlib.Path,
                version: str = DEFAULT_SIL_BUILDTASKS_VERSION,
                nuget_source: str | None = None) -> None:
    """Verify the GUID files describe the payload, without writing anything.

    Runs MakeWixForDirTree again with CheckOnly, which allocates nothing and
    errors out naming what it would have had to allocate. It runs straight
    after regenerate_guids(), where everything has just been allocated, so any
    complaint means something is wrong rather than something is new.

    It catches three things the regeneration does not report:

      * a file with no GUID anywhere, meaning the task did not write what it
        said it wrote;
      * a GUID held only in a per-directory .guidsForInstaller.xml and not in
        the consolidated one, which is what tells you whether those files can
        finally be deleted;
      * a per-directory file and the consolidated file disagreeing about an
        id, which is two GUIDs claiming one installer component.

    Skipped along with the regeneration under --no-regen-guids, since it needs
    the same SDK and package.
    """
    project = here / REGEN_PROJECT
    print("\nchecking the GUID files")

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
        die("the payload and its GUID files disagree; see above. It must not"
            " be committed\n       until this passes -- an id allocated now and"
            " lost later is a component\n       that changes identity next"
            " build.", payload_wiped=True)
    print("  every file has a GUID, all of them in the consolidated file")


def hg_command(repo: pathlib.Path, *args: str) -> str:
    """Run hg in *repo* and return its output."""
    result = subprocess.run(["hg", "--repository", str(repo), *args],
                            capture_output=True, text=True)
    if result.returncode != 0:
        die("hg %s in %s failed: %s" % (" ".join(args), repo, result.stderr.strip()))
    return result.stdout.strip()


def _working_copy_changes(repo: pathlib.Path) -> list:
    """What hg status reports: modified, added, removed, missing, unknown."""
    return [line for line in hg_command(repo, "status").splitlines() if line.strip()]


def build_staging_tree(hg: pathlib.Path, tag: str | None, target_triple: str,
                       stage: pathlib.Path, allow_dirty: bool = False
                       ) -> pathlib.Path:
    """Build Mercurial and return the staging tree its packaging produces.

    This drives Mercurial's own contrib/packaging code rather than
    reimplementing it, so the result is by construction the install layout its
    Inno Setup and WiX installers are built from.
    """
    packaging = hg / "contrib" / "packaging"
    if not (packaging / "hgpackaging" / "pyoxidizer.py").is_file():
        die("%s does not look like a Mercurial checkout (no hgpackaging)" % hg)

    # Before the update, not after. `hg update` carries compatible edits
    # across rather than refusing, and `identify --tags` prints the tag with no
    # sign of them -- the + that marks a dirty working copy is on the node id,
    # which --tags does not show. So a modified checkout would build, ship its
    # modifications, and report itself as the tag.
    changes = _working_copy_changes(hg)
    if changes and not allow_dirty:
        print("error: %s has uncommitted changes:" % hg, file=sys.stderr)
        for line in changes[:15]:
            print("  %s" % line, file=sys.stderr)
        if len(changes) > 15:
            print("  ... and %d more" % (len(changes) - 15), file=sys.stderr)
        die("the build installs the working copy, not the tag, so these would"
            " ship.\n       Commit, revert or shelve them, or pass"
            " --allow-dirty.")

    if tag:
        print("updating %s to %s" % (hg, tag))
        hg_command(hg, "update", tag)

    dirty = bool(_working_copy_changes(hg))
    tags = hg_command(hg, "identify", "--tags").split()
    print("building Mercurial %s%s for %s"
          % (" ".join(tags) or "(untagged)",
             " plus uncommitted changes" if dirty else "", target_triple))
    if dirty:
        print("warning: building a modified checkout; this payload is not the"
              " tag it names.\n         setuptools_scm marks"
              " mercurial/__version__.py, MercurialVersion will not.")
    if tag and DEFAULT_HG_TAG not in tags:
        print("note: building %s, not the %s this payload is meant to be"
              % (tag, DEFAULT_HG_TAG))
    elif not tag and DEFAULT_HG_TAG not in tags:
        print("warning: checkout is not at %s; pass --tag %s to update it"
              % (DEFAULT_HG_TAG, DEFAULT_HG_TAG))

    sys.path.insert(0, str(packaging))
    from hgpackaging import pyoxidizer as hgpyoxidizer

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

    return stage


@contextlib.contextmanager
def documentation_build_skipped(module):
    """Neuter the HTML documentation step in the layout build.

    create_pyoxidizer_install_layout() calls build_docs_html(), which shells
    out to `setup.py build_doc --html` and needs docutils importable by
    whichever interpreter runs this script. Upstream gets that from a bootstrap
    venv we never create.

    Nothing misses it: the only rule that consumes the result is a doc/*.html
    glob, which then matches nothing, and doc/ is dropped anyway.
    """
    original = module.build_docs_html

    def skipped(source_dir):
        print("  skipping the HTML docs; doc/ is not shipped")

    module.build_docs_html = skipped
    try:
        yield
    finally:
        module.build_docs_html = original


def assemble_payload(stage: pathlib.Path, payload: pathlib.Path,
                     trim: bool = True, trim_hgext: bool = True,
                     trim_sources: bool = True, force: bool = False
                     ) -> tuple[list[str], list[str], int, dict, set]:
    """Replace *payload* with the wanted part of *stage*, keeping our own files."""
    # The payload is emptied before the staging tree is copied into it, so if
    # the two overlap the delete takes the files about to be copied.
    here, there = stage.resolve(), payload.resolve()
    if here == there or there in here.parents or here in there.parents:
        die("the staging tree and the payload overlap:\n"
            "         stage   %s\n         payload %s\n"
            "       Emptying the payload would delete the files being copied"
            " from it." % (here, there))

    # Emptying the payload deletes whatever --output names, so anything that
    # is not recognisably a payload has to say so first. An empty directory,
    # or none at all, is the ordinary case for a new --output.
    if payload.exists():
        if not payload.is_dir():
            die("%s is not a directory; --output names the payload directory"
                " to rebuild." % there)
        contents = sorted(p.name for p in payload.iterdir())
        if contents and not force and not any(
                name in ("hg.exe", "mercurial.ini") or _is_guid_file(name)
                for name in contents):
            die("%s holds no hg.exe, mercurial.ini or GUID file, so it does"
                " not look\n       like a payload to replace:\n         %s\n"
                "       Refusing to empty it. Pass --force if it really is the"
                " one to rebuild."
                % (there, ", ".join(contents[:8])
                   + (", ..." if len(contents) > 8 else "")))

    def files_in(root: pathlib.Path) -> set[str]:
        if not root.exists():
            return set()
        return {str(p.relative_to(root)).replace(os.sep, "/")
                for p in root.rglob("*") if p.is_file()}

    before = files_in(payload)

    # Every GUID file, wherever it sits, is carried across and restored
    # afterwards: a GUID must stay attached to its path for the life of the
    # product, and outlive the file it describes so the installer can still
    # remove it.
    guids = {relative: (payload / relative).read_bytes()
             for relative in sorted(before)
             if _is_guid_file(relative.rsplit("/", 1)[-1])}

    with tempfile.TemporaryDirectory() as tmp:
        kept = pathlib.Path(tmp)
        missing = []
        for relative in PRESERVE:
            source = payload / relative
            if not source.is_file():
                missing.append(relative)
                continue
            destination = kept / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        if missing:
            print("warning: not in the old payload, so not in the new one: %s"
                  "\n         Nothing stages these; they come only from the"
                  " payload they are copied out of." % ", ".join(missing))

        if payload.exists():
            shutil.rmtree(payload)
        payload.mkdir(parents=True)

        dropped = 0
        trimmed: dict = {}
        removed_names: set = set()
        compiled = _sources_with_bytecode(stage) if trim_sources else set()
        uncompiled = 0
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
            destination = payload / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

        if uncompiled:
            print("note: kept %d .py with no bytecode beside them" % uncompiled)

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
        "--allow-dirty", action="store_true",
        help="build even though the Mercurial checkout has uncommitted"
             " changes, which the payload will then contain",
    )
    parser.add_argument(
        "--output",
        help="payload directory to refresh, which is emptied first (default:"
             " win/Mercurial beside this script)",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="empty the --output directory even though nothing in it looks"
             " like a payload",
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

    global PAYLOAD_PATH
    try:
        PAYLOAD_PATH = payload.relative_to(here).as_posix()
    except ValueError:
        PAYLOAD_PATH = None

    if os.name != "nt":
        die("building Mercurial for Windows needs Windows")
    hg = (pathlib.Path(args.hg_source).resolve() if args.hg_source
          else here.parent / "hg")
    if not hg.is_dir():
        die("no Mercurial checkout at %s (pass --hg-source)" % hg)
    stage = build_staging_tree(hg, args.tag, args.target_triple,
                               here / "build" / "stage", args.allow_dirty)

    if not (stage / "hg.exe").is_file():
        die("the build produced no hg.exe in %s" % stage)

    # --no-trim turns everything off, so that it still means "the install
    # layout exactly as staged"; the other two switch off one pass each.
    trim = not args.no_trim
    trim_hgext = trim and not args.no_trim_hgext
    trim_sources = trim and not args.no_trim_sources

    added, removed, dropped, trimmed, removed_names = assemble_payload(
        stage, payload, trim=trim, trim_hgext=trim_hgext,
        trim_sources=trim_sources, force=args.force)

    check_native_dependencies(payload, removed_names)
    check_python_imports(stage, trim, trim_hgext)

    if trimmed:
        total_files = sum(n for n, _ in trimmed.values())
        total_bytes = sum(b for _, b in trimmed.values())
        print("\ntrimmed %d file(s), %.1f MB Chorus cannot reach:"
              % (total_files, total_bytes / 1e6))
        for reason, (count, size) in sorted(trimmed.items(),
                                            key=lambda kv: -kv[1][1]):
            print("  %6.1f MB  %4d file(s)  %s" % (size / 1e6, count, reason))
        if not trim_hgext:
            print("  (--no-trim-hgext: unused extensions kept)")
        if not trim_sources:
            print("  (--no-trim-sources: .py sources kept)")
    elif args.no_trim:
        print("\ntrimming disabled")

    regenerated = not args.no_regen_guids
    if regenerated:
        regenerate_guids(here, payload, args.sil_buildtasks_version,
                         args.nuget_source)
        check_guids(here, payload, args.sil_buildtasks_version,
                    args.nuget_source)

    total = sum(1 for p in payload.rglob("*") if p.is_file())
    directories = len({p.parent for p in payload.rglob("*") if p.is_file()})
    left_out = dropped + sum(n for n, _ in trimmed.values())
    print("\n%s rebuilt: %d file(s) in %d director(ies), %d left out"
          % (payload, total, directories, left_out))
    print("  %d added, %d removed since the last build" % (len(added), len(removed)))
    for name in added:
        print("    + %s" % name)
    for name in removed:
        print("    - %s" % name)

    print(
        "\nNext steps (README-Windows.md has the rest):\n"
        "  1. Smoke-test:  %s\\hg.exe version\n"
        "  2. hg.exe and lib/ differ byte-for-byte at the same tag; judge the\n"
        "     build by the file list, not the diff.\n"
        "  3. %s"
        % (payload,
           "Commit the payload with .guidsForInstaller.all.xml and the"
           " per-directory files."
           if regenerated else
           "Re-run without --no-regen-guids before committing this payload.")
    )


if __name__ == "__main__":
    main()