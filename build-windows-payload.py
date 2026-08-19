#!/usr/bin/env python3
"""Rebuild win/Mercurial by building TortoiseHg from source.

MUST BE RUN ON WINDOWS for the build itself -- it drives py2exe through
TortoiseHg's own packaging code, which needs MSVC and a Python 3.9 x64
interpreter. The staging step afterwards is plain file copying and runs
anywhere, which is what --from-stage exists for.

Why TortoiseHg and not Mercurial: TortoiseHg's Windows build is py2exe, which
puts the whole pure-Python tree into a single lib/library.zip and leaves a flat
lib/ beside it. Mercurial's own Windows build is PyOxidizer, whose hg.exe
resolves modules from an index of concrete paths under lib/, so it cannot be
zipped and produces a directory per package. The flat layout matters here: the
Chorus installer records one .guidsForInstaller.xml per directory, so the
PyOxidizer payload needs about a hundred of them against six for this one.

The build is not reproducible. Comparing the x86 and x64 MSIs of one TortoiseHg
release shows 59 of 1479 library.zip members differing, 47 at identical length,
including pure-stdlib modules such as difflib.pyc whose bytecode cannot depend
on the target architecture. That is nondeterministic ordering of set/frozenset
constants in marshalled code objects. Expect the file list to match exactly and
the bytes not to.

Usage::

    py -3 build-windows-payload.py                       # build ../thg as checked out
    py -3 build-windows-payload.py --tag 7.2.2           # update it to a tag first
    py -3 build-windows-payload.py --from-stage DIR      # reuse an existing staging tree

The payload is assembled from the staging tree TortoiseHg's own
stage_install() produces, which is also exactly what its MSI is built from, so
--from-stage can be pointed at an MSI extracted with `msiexec /a` to check the
selection rules without running a build.
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

# TortoiseHg 7.0.1 bundled these. Pinning keeps a rebuild honest; pass --hg-tag
# and --evolve-rev to move them.
DEFAULT_HG_TAG = "7.0.1"
DEFAULT_EVOLVE_REV = "62f31db54459"

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

# What TortoiseHg's staging tree holds that this payload does not want. Written
# as exclusions rather than an allowlist so that a file a later Mercurial adds
# is shipped by default; the risk the other way is that a new TortoiseHg GUI
# file slips in, which the summary printed at the end is there to catch.
#
# Derived from the TortoiseHg 7.0.1 x64 MSI: applying these to its 432-file
# payload leaves exactly the 86 files this repository takes from it.

# TortoiseHg's own programs, the shell extension, and two files that only
# describe the TortoiseHg product.
DROP_ROOT_FILES = [
    "COPYING.txt",
    "docdiff.exe",
    "extension-versions.txt",
    "Pageant.exe",
    "thg.exe",
    "thgw.exe",
    "TortoiseHgOverlayServer.exe",
]

# The shell extension is named per architecture (ThgShellx64.dll on x64,
# ThgShellx86.dll on x86, and the x64 installer ships both), so match the stem.
DROP_ROOT_PREFIXES = ["ThgShell"]

# Whole directories: the Qt plugin trees, TortoiseHg's icons and translations,
# and the Mercurial data files Chorus has never shipped. templates/ and
# helptext/ only matter for `hg log --style=X` and `hg help`; Chorus passes
# inline --template strings and never reads hg's prose.
DROP_DIRECTORIES = [
    "diff-scripts",
    "doc",
    "helptext",
    "i18n",
    "icons",
    "imageformats",
    "locale",
    "platforms",
    "styles",
    "templates",
]

# Everything under lib/ is kept except the GUI stack: Qt5 itself, the PyQt5
# bindings, pygit2 and its native git2.dll, TortoisePlink, and the qt.conf that
# only exists so kdiff3 can find the Qt plugins. kdiff3.exe and spawn.cmd stay,
# because the payload has always carried them.
DROP_LIB_PREFIXES = ["Qt5", "PyQt5", "pygit2", "git2.dll", "TortoisePlink", "qt.conf"]


# TortoiseHg's own WiX renames these on the way into its MSI, via Name= on the
# File element -- see win32/wix/tortoisehg-py3.wxs, e.g.
#
#     <File Id='terminaltools.rc' Name='TerminalTools.rc'
#           Source='contrib\terminaltools.rc' />
#
# so the staging tree carries the lowercase repository names while every payload
# ever shipped carries the mixed-case ones. Renaming here keeps that continuity.
# It is not cosmetic: MakeWixForDirTree derives each File Id from the name on
# disk, so shipping terminaltools.rc would mint a new component GUID for a file
# that already has one, and silently change its installer identity. Windows
# being case-insensitive, git would not even show the rename.
STAGE_RENAMES = {
    "defaultrc/editortools.rc": "defaultrc/EditorTools.rc",
    "defaultrc/mercurial.rc": "defaultrc/Mercurial.rc",
    "defaultrc/mergepatterns.rc": "defaultrc/MergePatterns.rc",
    "defaultrc/mergetools.rc": "defaultrc/MergeTools.rc",
    "defaultrc/terminaltools.rc": "defaultrc/TerminalTools.rc",
}


def is_dropped(relative: str) -> bool:
    """Should this staging-tree path be left out of the payload?"""
    head = relative.split("/")[0]
    if head in DROP_DIRECTORIES:
        return True
    if "/" not in relative:
        return (relative in DROP_ROOT_FILES
                or any(relative.startswith(p) for p in DROP_ROOT_PREFIXES))
    if head == "lib":
        leaf = relative.split("/", 1)[1]
        return any(leaf.startswith(prefix) for prefix in DROP_LIB_PREFIXES)
    return False



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
    is a rename, and has just been handed a second installer identity. That is
    what happens when the staging tree's lowercase .rc names reach the payload,
    which STAGE_RENAMES exists to prevent.

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
              " given a second\n  installer identity -- fix the name instead, see"
              " STAGE_RENAMES.")
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


def build_staging_tree(thg: pathlib.Path, tag: str | None, hg_tag: str,
                       evolve_rev: str, stage: pathlib.Path) -> pathlib.Path:
    """Build TortoiseHg and return the staging tree its packaging produces.

    This drives TortoiseHg's own contrib/packaging code rather than
    reimplementing it, so the result is by construction what its MSI is built
    from. Only the two steps that exist purely to make an installer are left
    out: the WiX link, and the C++ shell extension, whose ThgShell*.dll this
    payload drops anyway.
    """
    packaging = thg / "contrib" / "packaging"
    if not (packaging / "thgpackaging" / "py2exe.py").is_file():
        die("%s does not look like a TortoiseHg checkout (no thgpackaging)" % thg)

    if tag:
        print("updating %s to %s" % (thg, tag))
        hg_command(thg, "update", tag)
    print("building TortoiseHg %s" % hg_command(thg, "identify", "--tags"))

    sys.path.insert(0, str(packaging))
    from thgpackaging import cli as thgcli, py2exe as thgpy2exe, util as thgutil

    # TortoiseHg clones Mercurial, evolve and the shell extension next to
    # itself. Pinned rather than left on 'stable' so a rebuild of an old tag
    # gets the Mercurial that tag shipped with.
    print("staging dependency repositories (this clones several repos)")
    thgcli.stage_dependencies(hg_version=hg_tag, evolve_version=evolve_rev,
                              shellext_version="default", clean=False)

    source_dirs = thgutil.SourceDirs(thg)

    # These must match what thgpackaging/wix.py passes, or library.zip comes out
    # with a different module set: dulwich, keyring, pygments and win32ctypes are
    # all in the shipped zip only because they are named here, and py2exe on
    # Python 3 does not find _curses_panel by itself.
    from thgpackaging import wix as thgwix

    with documentation_builds_skipped(thgpy2exe):
        thgpy2exe.build_py2exe(
            source_dirs,
            thg / "build",
            pathlib.Path(sys.executable),
            "wix",
            packaging / "requirements-windows-pyqt5-installer.txt",
            extra_packages=set(thgwix.EXTRA_PACKAGES),
            extra_includes=set(thgwix.EXTRA_INCLUDES),
        )

    if stage.exists():
        shutil.rmtree(stage)
    thgpy2exe.stage_install(source_dirs, stage, lower_case=True)

    # kdiff3.exe and spawn.cmd come from the thg-winbuild repository via
    # EXTRA_INSTALL_RULES in thgpackaging/wix.py, which runs as part of the
    # installer build we are skipping. The payload has always shipped both.
    winbuild = source_dirs.winbuild
    for source, destination in (
        (winbuild / "contrib" / "kdiff3x64.exe", stage / "lib" / "kdiff3.exe"),
        (winbuild / "contrib" / "spawn.cmd", stage / "lib" / "spawn.cmd"),
    ):
        if not source.is_file():
            die("%s is missing; is the thg-winbuild clone complete?" % source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    return stage


@contextlib.contextmanager
def documentation_builds_skipped(module):
    """Neuter the two documentation steps in build_py2exe for the duration.

    Both build files this payload throws away, and both are the most fragile
    links in the pipeline:

      * `build chm` in thg/doc needs HTML Help Workshop, a discontinued
        Microsoft download, and build_py2exe refuses to start without hhc.exe
        on PATH even though the .chm it produces only ever lands in doc/.

      * `make -C doc html` in the Mercurial clone needs docutils importable by
        whichever interpreter is running this script. Upstream gets that from
        the bootstrap venv packaging.py creates from requirements.txt; we call
        build_py2exe directly and so never have it.

    Nothing downstream misses either: the only staging rules that consume them
    are a doc/*.html glob and a TortoiseHg.chm copy, and doc/ is dropped.

    The patches go onto the shared shutil and subprocess modules, since that is
    what the packaging code holds references to, and are undone on the way out.
    """
    original_which = module.shutil.which
    original_run = module.subprocess.run

    def which(name, *args, **kwargs):
        if name == "hhc.exe":
            return "hhc.exe-not-needed"
        return original_which(name, *args, **kwargs)

    def run(command, *args, **kwargs):
        skipped = None
        if isinstance(command, (list, tuple)):
            head = [str(part) for part in command[:4]]
            if head[:2] == ["build", "chm"]:
                skipped = "the TortoiseHg .chm"
            elif head == ["make", "-C", "doc", "html"]:
                skipped = "Mercurial's HTML documentation"
        if skipped:
            print("  skipping %s; doc/ is not shipped" % skipped)
            return subprocess.CompletedProcess(command, 0)
        return original_run(command, *args, **kwargs)

    module.shutil.which = which
    module.subprocess.run = run
    try:
        yield
    finally:
        module.shutil.which = original_which
        module.subprocess.run = original_run


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
        renamed = 0
        for source in sorted(stage.rglob("*")):
            if not source.is_file():
                continue
            relative = str(source.relative_to(stage)).replace(os.sep, "/")
            if is_dropped(relative):
                dropped += 1
                continue
            # A staging tree taken from an unpacked MSI already carries the
            # renamed names, so the lookup simply misses and nothing happens.
            if relative in STAGE_RENAMES:
                relative = STAGE_RENAMES[relative]
                renamed += 1
            destination = payload / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
        if renamed:
            print("renamed %d staged file(s) to the names the MSI ships" % renamed)

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
        "--thg-source",
        help="TortoiseHg checkout to build (default: ../thg beside this repository)",
    )
    parser.add_argument(
        "--tag",
        help="update the TortoiseHg checkout to this tag before building",
    )
    parser.add_argument(
        "--hg-tag", default=DEFAULT_HG_TAG,
        help="Mercurial tag to bundle (default: %(default)s)",
    )
    parser.add_argument(
        "--evolve-rev", default=DEFAULT_EVOLVE_REV,
        help="evolve revision to bundle (default: %(default)s)",
    )
    parser.add_argument(
        "--from-stage", metavar="DIR",
        help="skip the build and take the payload from an existing staging tree,"
             " such as a TortoiseHg MSI unpacked with `msiexec /a`",
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
            die("building TortoiseHg needs Windows; use --from-stage to assemble"
                " a payload from a tree built elsewhere")
        thg = (pathlib.Path(args.thg_source).resolve() if args.thg_source
               else here.parent / "thg")
        if not thg.is_dir():
            die("no TortoiseHg checkout at %s (pass --thg-source)" % thg)
        stage = build_staging_tree(thg, args.tag, args.hg_tag, args.evolve_rev,
                                   here / "build" / "stage")

    if not (stage / "hg.exe").is_file():
        die("no hg.exe in %s; is that really a TortoiseHg staging tree?" % stage)

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
        "  2. Expect library.zip and the binaries to differ byte-for-byte from\n"
        "     the last build even at the same tag; the file list should not.\n"
        "  3. Set MercurialVersion in SIL.Chorus.Mercurial.csproj, update the\n"
        "     mercurial-version matrix in .github/workflows/nuget-ci-cd.yml, and\n"
        "     add a PackageReleaseNotes entry.\n"
        "  4. %s"
        % (payload,
           "Commit the refreshed .guidsForInstaller.xml files with this payload."
           if regenerated else
           "GUID regeneration was skipped; re-run without --no-regen-guids"
           " before committing this payload.")
    )


if __name__ == "__main__":
    main()
