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


def check(root: pathlib.Path) -> list:
    """Every reason the bytecode under *root* is not what it should be."""
    problems = []
    cache = root / CACHE

    for stray in sorted(root.rglob("*.pyo")):
        problems.append("%s: Python 2 bytecode, which no Python since 3.4 loads"
                        % stray)
    for stray in sorted(cache.glob("*.opt-*.pyc")):
        problems.append("%s: optimised bytecode, which the embedded interpreter"
                        " never looks for" % stray)

    sources = sorted(root.glob("*.py"))
    if not sources:
        problems.append("%s holds no Python sources; is the path right?" % root)

    for source in sources:
        pyc = cache / ("%s.%s.pyc" % (source.stem, TAG))
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

    for pyc in sorted(cache.glob("*.pyc")):
        if not (root / (pyc.name.split(".")[0] + ".py")).is_file():
            problems.append("%s: bytecode for a source that is gone" % pyc)

    return problems


def main() -> None:
    if struct.unpack_from("<H", importlib.util.MAGIC_NUMBER, 0)[0] != MAGIC:
        raise SystemExit(
            "error: this must run under CPython 3.9; %s hashes sources"
            " differently\n       and would call every file stale. Try"
            " `uv run --python 3.9 python %s`."
            % (".".join(str(n) for n in sys.version_info[:3]),
               pathlib.Path(__file__).name))

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

    print("fixutf8 bytecode: %d module(s), all checked-hash and matching their"
          " sources" % len(sorted(root.glob("*.py"))))


if __name__ == "__main__":
    main()