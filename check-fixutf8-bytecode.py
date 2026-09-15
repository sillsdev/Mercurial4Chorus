#!/usr/bin/env python3
"""Verify the committed fixutf8 bytecode against its sources.

MUST BE RUN UNDER CPYTHON 3.9. importlib's source hash is keyed by the
interpreter's magic number, so any other version calls every file stale. 3.9 is
also what rust/hgcli/pyoxidizer.bzl embeds, and therefore the only bytecode
hg.exe will ever load:

    uv run --python 3.9 python check-fixutf8-bytecode.py

The .pyc must be PEP 552 checked-hash. A timestamp-based one records the source
mtime, git does not preserve mtimes, so it is rejected on every machine and hg
recompiles the extension on every invocation -- 290 ms of it, on a payload
Chorus runs many times per operation, and every time where the install
directory is not writable. Regenerate with:

    uv run --python 3.9 python -m compileall -f \\
        --invalidation-mode checked-hash MercurialExtensions/fixutf8
    git add -f MercurialExtensions/fixutf8/__pycache__

The -f is needed: that directory's own .gitignore excludes *.pyc.
"""

from __future__ import annotations

import importlib.util
import pathlib
import struct
import sys

EXTENSION = pathlib.Path("MercurialExtensions") / "fixutf8"
CACHE = "__pycache__"

# The CPython that rust/hgcli/pyoxidizer.bzl asks PyOxidizer to embed.
MAGIC = 3425
TAG = "cpython-39"

# PEP 552: bit 0 makes the pyc hash-based rather than timestamp-based, bit 1
# makes the loader check that hash. We want both.
HASH_BASED = 0b01
CHECK_SOURCE = 0b10


def _sources(root: pathlib.Path) -> list:
    """Every module under *root*, at any depth, that wants bytecode."""
    return sorted(source for source in root.rglob("*.py")
                  if CACHE not in source.parts)


def check(root: pathlib.Path) -> list:
    """Every reason the bytecode under *root* is not what it should be."""
    problems = []

    for stray in sorted(root.rglob("*.pyo")):
        problems.append("%s: Python 2 bytecode, which no Python since 3.4"
                        " loads. Delete it." % stray)
    for stray in sorted(root.rglob("*.opt-*.pyc")):
        problems.append("%s: optimised bytecode, which the embedded interpreter"
                        " never looks for. Delete it." % stray)

    sources = _sources(root)
    if not sources:
        problems.append("%s holds no Python sources; is the path right?" % root)

    # cache_from_source() knows both the tag and where __pycache__ goes, so the
    # expected name is derived rather than spelled out again -- and anything
    # else found later is, by construction, not one of them.
    expected = {pathlib.Path(importlib.util.cache_from_source(str(source))): source
                for source in sources}

    for pyc, source in sorted(expected.items()):
        if not pyc.is_file():
            problems.append("%s: no bytecode beside %s" % (pyc, source.name))
            continue
        data = pyc.read_bytes()
        if len(data) < 16:
            problems.append("%s: too short to be a pyc" % pyc)
            continue
        magic = struct.unpack_from("<H", data, 0)[0]
        if magic != MAGIC:
            problems.append("%s: magic %d, not the %d of CPython 3.9"
                            % (pyc, magic, MAGIC))
            continue
        flags = struct.unpack_from("<I", data, 4)[0]
        if not flags & HASH_BASED:
            problems.append("%s: timestamp-based, so it is rejected on every"
                            " machine git checks it out on" % pyc)
            continue
        if not flags & CHECK_SOURCE:
            problems.append("%s: unchecked-hash, so an edit to %s would be"
                            " ignored" % (pyc, source.name))
            continue
        if importlib.util.source_hash(source.read_bytes()) != data[8:16]:
            problems.append("%s: does not match %s; it was compiled from"
                            " something else" % (pyc, source.name))

    for pyc in sorted(root.rglob("*.pyc")):
        if pyc in expected or ".opt-" in pyc.name:
            continue
        problems.append("%s: not bytecode for any source here, built as %s."
                        " Delete it."
                        % (pyc, pyc.name.split(".", 1)[-1].rsplit(".", 1)[0]))

    return problems


def main() -> None:
    if struct.unpack_from("<H", importlib.util.MAGIC_NUMBER, 0)[0] != MAGIC:
        raise SystemExit(
            "error: this must run under CPython 3.9; %s hashes sources"
            " differently\n       and would call every file stale. Try"
            " `uv run --python 3.9 python %s`."
            % (".".join(str(n) for n in sys.version_info[:3]),
               pathlib.Path(__file__).name))

    # The expected .pyc names come from cache_from_source(), which uses the
    # running interpreter's tag. MAGIC has already tied that to 3.9; this ties
    # TAG to it too, so the two ways of naming the same interpreter cannot
    # drift apart when the payload's Python moves.
    if sys.implementation.cache_tag != TAG:
        raise SystemExit(
            "error: this interpreter tags bytecode %s, but TAG says %s."
            % (sys.implementation.cache_tag, TAG))

    root = pathlib.Path(__file__).resolve().parent / EXTENSION
    problems = check(root)
    if problems:
        print("error: the committed bytecode does not describe the sources:",
              file=sys.stderr)
        for problem in problems:
            print("  %s" % problem, file=sys.stderr)
        raise SystemExit(
            "\nRegenerate it:\n"
            "  uv run --python 3.9 python -m compileall -f"
            " --invalidation-mode checked-hash %s\n"
            "  git add -f %s\n"
            "The -f matters: that directory's .gitignore excludes *.pyc."
            % (EXTENSION, EXTENSION / CACHE))

    print("fixutf8 bytecode: %d module(s), all %s, checked-hash and matching"
          " their sources" % (len(_sources(root)), TAG))


if __name__ == "__main__":
    main()