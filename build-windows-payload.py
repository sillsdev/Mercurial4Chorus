#!/usr/bin/env python3
"""Rebuild win/Mercurial by building Mercurial from source.

MUST BE RUN ON WINDOWS for the build itself -- it drives PyOxidizer through
Mercurial's own packaging code, which needs Rust, PyOxidizer and MSVC. The
staging step afterwards is plain file copying and runs anywhere, which is what
--from-stage exists for.

The build is Mercurial's own supported Windows build: the same
hgpackaging.pyoxidizer.create_pyoxidizer_install_layout() that its Inno Setup
and WiX installers are built from. Driving that rather than reimplementing it
means the staging tree is by construction the tree upstream ships, so
--from-stage can be pointed at a Mercurial MSI unpacked with `msiexec /a` to
check the selection rules without running a build.

What that costs, and why it is worth knowing before touching this file: hg.exe
resolves modules through oxidized_importer from an index of concrete paths
baked into the executable. lib/ therefore cannot be zipped or rearranged, and
expands to a directory per package -- roughly a hundred of them. The Chorus
installer records one .guidsForInstaller.xml per directory and every entry has
to be kept forever, so the payload carries about a hundred of those files
rather than the six a py2exe layout needed. That is the price of building from
Mercurial itself instead of from a third party's repackaging of it.

The build is not reproducible. Expect the file list to match a previous build
at the same tag and the bytes not to; judge a rebuild by the added/removed
summary printed at the end, not by which blobs moved.

Usage::

    py -3 build-windows-payload.py                       # build ../hg as checked out
    py -3 build-windows-payload.py --tag 7.0.1           # update it to a tag first
    py -3 build-windows-payload.py --from-stage DIR      # reuse an existing staging tree
"""

from __future__ import annotations

import argparse
import contextlib
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

# The Mercurial tag this payload is meant to be. Not applied automatically --
# --tag does that -- but the build warns when the checkout is somewhere else,
# so a stale ../hg cannot quietly change what gets built. Keep this in step
# with MercurialVersion in SIL.Chorus.Mercurial.csproj and the
# mercurial-version matrix in .github/workflows/nuget-ci-cd.yml.
DEFAULT_HG_TAG = "7.0.1"

# PyOxidizer needs the target named explicitly. Unlike a py2exe build, the
# architecture does not come from the interpreter this script is launched with:
# PyOxidizer downloads and embeds its own CPython 3.9, which is also why the
# committed fixutf8 .pyc files stay valid for cpython-39.
DEFAULT_TARGET_TRIPLE = "x86_64-pc-windows-msvc"
TARGET_TRIPLES = ["i686-pc-windows-msvc", "x86_64-pc-windows-msvc"]

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

# Per-directory record of the MSI component GUID assigned to each file. These
# are NOT listed in PRESERVE: they are found by searching the existing payload,
# so a GUID file in any directory is carried across, however the layout moves.
# That matters more here than it ever did, because moving to the PyOxidizer
# layout moves nearly every path in the payload at once.
#
# They must survive verbatim. A GUID has to stay attached to its path for the
# life of the product, and an entry has to outlive the file it describes: the
# Chorus installer needs the old GUID to recognise and remove a file a later
# Mercurial no longer ships. SIL.BuildTasks' MakeWixForDirTree only ever
# appends, so keeping the old file is what makes both properties hold.
GUID_FILE = ".guidsForInstaller.xml"


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

# Nothing under lib/ is dropped. It is the frozen application's own module
# tree -- an index of concrete paths baked into hg.exe -- and pruning pieces of
# it is how a frozen application breaks. That includes the third-party packages
# rust/hgcli/pyoxidizer.bzl pip-installs from requirements-windows-py3.txt on
# Windows "for convenience"; they are part of what upstream ships.


def is_dropped(relative: str) -> bool:
    """Should this staging-tree path be left out of the payload?"""
    if relative in DROP_FILES:
        return True
    if relative.split("/")[0] in DROP_DIRECTORIES:
        return True
    if "/" not in relative:
        return relative in DROP_ROOT_FILES
    return False


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


def _guid_entries(payload: pathlib.Path) -> dict:
    """Every File Id recorded in the payload, mapped to the file recording it."""
    entries = {}
    for path in sorted(payload.rglob(GUID_FILE)):
        text = path.read_text(encoding="utf-8-sig", errors="replace")
        for identifier in re.findall(r'Id="([^"]+)"', text):
            entries[identifier] = path
    return entries


def regenerate_guids(here: pathlib.Path, payload: pathlib.Path) -> int:
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

    print("\nregenerating installer GUIDs")
    before_entries = _guid_entries(payload)
    before_bytes = {path: path.read_bytes()
                    for path in sorted(payload.rglob(GUID_FILE))}

    # -restore in the same invocation: the task assembly arrives via
    # PackageReference, and its UsingTask via the package's own props.
    run(["dotnet", "msbuild", project, "-restore", "-nologo",
         "-t:RegenerateGuids",
         "-p:PayloadDir=%s" % payload],
        cwd=here, what="dotnet msbuild -t:RegenerateGuids")

    after = sorted(payload.rglob(GUID_FILE))
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
    return changed


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


def assemble_payload(stage: pathlib.Path, payload: pathlib.Path
                     ) -> tuple[list[str], list[str], int]:
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
             for relative in sorted(before) if relative.endswith(GUID_FILE)}

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

        dropped = 0
        for source in sorted(stage.rglob("*")):
            if not source.is_file():
                continue
            relative = str(source.relative_to(stage)).replace(os.sep, "/")
            if is_dropped(relative):
                dropped += 1
                continue
            destination = payload / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)

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
        print("guids: carried %d %s file(s) across" % (len(guids), GUID_FILE))

    after = files_in(payload)
    return sorted(after - before), sorted(before - after), dropped


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

    added, removed, dropped = assemble_payload(stage, payload)

    regenerated = not args.no_regen_guids
    if regenerated:
        regenerate_guids(here, payload)

    total = sum(1 for p in payload.rglob("*") if p.is_file())
    print("\n%s rebuilt: %d file(s), %d left out of the staging tree"
          % (payload, total, dropped))
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
