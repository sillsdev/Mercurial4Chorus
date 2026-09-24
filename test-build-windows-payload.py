#!/usr/bin/env python3
"""Tests for the parts of build-windows-payload.py that can run anywhere.

    python3 test-build-windows-payload.py

check_python_imports() is the reason this file exists. It decides whether a
trim rule has removed a package that a module the payload keeps still imports,
and the answer turns on which statements run when a module is imported. Getting
that wrong is silent: the build passes and hg raises ImportError on a user's
machine. So every construct it has to see, and every one it must ignore, is
pinned here.

Nothing in this file touches Windows, a Mercurial checkout or the network. The
staging trees are a couple of files in a temporary directory.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import pathlib
import struct
import sys
import tempfile
import unittest

# Loading build-windows-payload.py would otherwise leave a __pycache__ in the
# repository root, which is not ignored -- the payload's own __pycache__ is
# tracked, so *.pyc cannot be.
sys.dont_write_bytecode = True

HERE = pathlib.Path(__file__).resolve().parent


def _load(name: str):
    """Import a hyphenated script beside this file."""
    path = HERE / ("%s.py" % name)
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bwp = _load("build-windows-payload")

# A package every trim rule removes in full, and one the payload keeps.
REMOVED = "fuzzywuzzy"        # in TRIM_UNIMPORTED_PACKAGES
REMOVED_LAYOUT = ("lib/%s/__init__.py" % REMOVED,)
KEPT = "lib/mercurial/probe.py"


class StageMixin:
    """A two-file staging tree: one package trimmed away, one module kept."""

    def stage_with(self, source: str, module: str = KEPT,
                   removed: tuple = REMOVED_LAYOUT) -> pathlib.Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        stage = pathlib.Path(tmp.name)
        for relative in removed:
            gone = stage / relative
            gone.parent.mkdir(parents=True, exist_ok=True)
            gone.write_text("")
        kept = stage / module
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_text(source + "\n")
        return stage

    def check(self, source: str, module: str = KEPT,
              removed: tuple = REMOVED_LAYOUT) -> bool:
        """Does check_python_imports() reject a payload built from *source*?"""
        stage = self.stage_with(source, module, removed)
        noise = io.StringIO()
        try:
            with contextlib.redirect_stdout(noise), contextlib.redirect_stderr(noise):
                bwp.check_python_imports(stage, True, True)
        except SystemExit:
            return True
        return False

    def assertRejected(self, source: str, module: str = KEPT,
                       removed: tuple = REMOVED_LAYOUT) -> None:
        self.assertTrue(self.check(source, module, removed),
                        "should have been rejected")

    def assertAccepted(self, source: str, module: str = KEPT,
                       removed: tuple = REMOVED_LAYOUT) -> None:
        self.assertFalse(self.check(source, module, removed),
                         "should have been accepted")


class ImportsThatRun(StageMixin, unittest.TestCase):
    """Every module-level construct whose body runs on import."""

    def test_top_level(self):
        self.assertRejected("import %s" % REMOVED)

    def test_from_import(self):
        self.assertRejected("from %s import thing" % REMOVED)

    def test_dotted_import(self):
        self.assertRejected("import %s.submodule" % REMOVED)

    def test_if_body(self):
        self.assertRejected("if True:\n    import %s" % REMOVED)

    def test_if_else(self):
        self.assertRejected("if False:\n    pass\nelse:\n    import %s" % REMOVED)

    def test_for_body(self):
        self.assertRejected("for _ in (1,):\n    import %s" % REMOVED)

    def test_for_else(self):
        self.assertRejected("for _ in ():\n    pass\nelse:\n    import %s" % REMOVED)

    def test_while_body(self):
        self.assertRejected("while True:\n    import %s\n    break" % REMOVED)

    def test_while_else(self):
        self.assertRejected("while False:\n    pass\nelse:\n    import %s" % REMOVED)

    def test_with_body(self):
        self.assertRejected("import contextlib\nwith contextlib.suppress():\n"
                            "    import %s" % REMOVED)

    @unittest.skipUnless(sys.version_info >= (3, 10), "match needs 3.10")
    def test_match_case(self):
        self.assertRejected("match 1:\n    case 1:\n        import %s" % REMOVED)

    def test_nested_compound_statements(self):
        self.assertRejected(
            "import contextlib\nwith contextlib.suppress():\n    for _ in (1,):\n"
            "        while True:\n            import %s\n            break" % REMOVED)

    def test_else_of_a_typechecking_guard_still_runs(self):
        self.assertRejected("import typing\nif typing.TYPE_CHECKING:\n    pass\n"
                            "else:\n    import %s" % REMOVED)


class ImportsThatDoNot(StageMixin, unittest.TestCase):
    """The exclusions, which are as load-bearing as the inclusions."""

    def test_try_body(self):
        self.assertAccepted("try:\n    import %s\nexcept ImportError:\n    pass" % REMOVED)

    def test_except_handler(self):
        self.assertAccepted("try:\n    pass\nexcept Exception:\n    import %s" % REMOVED)

    def test_try_nested_in_a_loop(self):
        self.assertAccepted("for _ in (1,):\n    try:\n        import %s\n"
                            "    except ImportError:\n        pass" % REMOVED)

    def test_function_body(self):
        self.assertAccepted("def f():\n    import %s" % REMOVED)

    def test_function_nested_in_a_loop(self):
        self.assertAccepted("for _ in (1,):\n    def f():\n        import %s" % REMOVED)

    def test_class_body(self):
        self.assertAccepted("class C:\n    import %s" % REMOVED)

    def test_typechecking_attribute(self):
        self.assertAccepted("import typing\nif typing.TYPE_CHECKING:\n"
                            "    import %s" % REMOVED)

    def test_typechecking_bare_name(self):
        self.assertAccepted("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n"
                            "    import %s" % REMOVED)


class WhatCountsAsRemoved(StageMixin, unittest.TestCase):
    def test_a_surviving_package_is_not_flagged(self):
        # lib/mercurial loses locale and templates but survives, so importing
        # it is not importing something that was removed.
        self.assertAccepted("import mercurial")

    def test_stdlib_is_not_flagged(self):
        self.assertAccepted("import os, sys")

    def test_allowlisted_module_may_import_a_removed_package(self):
        self.assertAccepted("import %s" % REMOVED,
                            module=sorted(bwp.IMPORT_ALLOWED)[0])

    def test_allowlist_covers_only_the_modules_it_names(self):
        allowed = sorted(bwp.IMPORT_ALLOWED)[0]
        sibling = allowed.rsplit("/", 1)[0] + "/not_allowlisted.py"
        self.assertRejected("import %s" % REMOVED, module=sibling)


class ShapesOfRemoval(StageMixin, unittest.TestCase):
    """The rules remove packages, plain files and compiled modules alike.

    The name an import uses is not the name on disk for the last two, and
    comparing the wrong one made two thirds of the trim lists invisible here.
    """

    def test_package_directory(self):
        self.assertRejected("import fuzzywuzzy",
                            removed=("lib/fuzzywuzzy/__init__.py",))

    def test_single_file_module(self):
        self.assertRejected("import six", removed=("lib/six.py",))

    def test_single_file_module_by_from_import(self):
        self.assertRejected("from six import moves", removed=("lib/six.py",))

    def test_compiled_module_with_an_abi_tag(self):
        self.assertRejected("import _curses",
                            removed=("lib/_curses.cp39-win_amd64.pyd",))

    def test_compiled_module_without_a_tag(self):
        self.assertRejected("import _msi", removed=("lib/_msi.pyd",))

    def test_a_dll_is_not_an_import_name(self):
        self.assertAccepted("import tcl86t", removed=("lib/tcl86t.dll",))

    def test_a_partly_trimmed_package_is_not_removed(self):
        # lib/mercurial keeps probe.py, so trimming its locale does not make
        # `import mercurial` an import of something gone.
        self.assertAccepted("import mercurial",
                            removed=("lib/mercurial/locale/x.rc",))


class Fixtures(unittest.TestCase):
    """The names these tests lean on really are the ones the rules remove."""

    def test_the_fixtures_are_trimmed_by_the_real_rules(self):
        for relative in ("lib/%s/__init__.py" % REMOVED, "lib/six.py",
                         "lib/_curses.cp39-win_amd64.pyd", "lib/_msi.pyd",
                         "lib/tcl86t.dll"):
            with self.subTest(relative):
                self.assertIsNotNone(bwp.is_trimmed(relative, True),
                                     "%s is no longer trimmed" % relative)

    def test_the_kept_module_really_is_kept(self):
        self.assertIsNone(bwp.is_trimmed(KEPT, True))
        self.assertFalse(bwp.is_dropped(KEPT))

    def test_no_allowlist_entry_is_dead(self):
        # pygments/sphinxext.py only survives under --no-trim-hgext, which is
        # the mode where it would be checked, so "kept in some mode" is the
        # test. An entry kept in none of them is one nothing can reach.
        for relative in sorted(bwp.IMPORT_ALLOWED):
            with self.subTest(relative):
                self.assertTrue(bwp.is_trimmed(relative, True) is None
                                or bwp.is_trimmed(relative, False) is None,
                                "%s is trimmed in every mode" % relative)


class ModuleOf(unittest.TestCase):
    """What an entry under lib/ is called when something imports it."""

    def test_names(self):
        for entry, want in (("fuzzywuzzy", "fuzzywuzzy"),
                            ("six.py", "six"),
                            ("_msi.pyd", "_msi"),
                            ("_curses.cp39-win_amd64.pyd", "_curses"),
                            ("tcl86t.dll", None),
                            ("zipp-3.1.0.dist-info", None)):
            with self.subTest(entry):
                self.assertEqual(bwp._module_of(entry), want)


class GuidEntries(StageMixin, unittest.TestCase):
    """Reading the GUID files, which now has to carry the GUID as well."""

    def guid_dir(self, files: dict) -> pathlib.Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = pathlib.Path(tmp.name)
        for name, entries in files.items():
            body = "".join('  <File Id="%s" Guid="%s" />\n' % pair for pair in entries)
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('<?xml version="1.0" encoding="utf-8"?>\n'
                            "<InstallerMetadata>\n%s</InstallerMetadata>\n" % body,
                            encoding="utf-8")
        return root

    def test_id_and_guid_are_both_read(self):
        root = self.guid_dir({".guidsForInstaller.all.xml": [("a", "G1")]})
        entries, conflicts = bwp._guid_entries(root)
        self.assertEqual(entries["a"][0], "G1")
        self.assertEqual(conflicts, [])

    def test_entries_are_unioned_across_files(self):
        root = self.guid_dir({".guidsForInstaller.all.xml": [("a", "G1")],
                              "sub/.guidsForInstaller.xml": [("b", "G2")]})
        entries, _ = bwp._guid_entries(root)
        self.assertEqual(sorted(entries), ["a", "b"])

    def test_the_same_guid_in_two_files_is_not_a_conflict(self):
        root = self.guid_dir({".guidsForInstaller.all.xml": [("a", "G1")],
                              "sub/.guidsForInstaller.xml": [("a", "G1")]})
        _, conflicts = bwp._guid_entries(root)
        self.assertEqual(conflicts, [])

    def test_two_files_disagreeing_is_a_conflict(self):
        root = self.guid_dir({".guidsForInstaller.all.xml": [("a", "G1")],
                              "sub/.guidsForInstaller.xml": [("a", "G2")]})
        _, conflicts = bwp._guid_entries(root)
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0][0], "a")

    def test_a_byte_order_mark_is_tolerated(self):
        root = self.guid_dir({".guidsForInstaller.all.xml": [("a", "G1")]})
        path = root / ".guidsForInstaller.all.xml"
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
        entries, _ = bwp._guid_entries(root)
        self.assertEqual(entries["a"][0], "G1")

    def test_malformed_xml_stops_the_build(self):
        root = self.guid_dir({".guidsForInstaller.all.xml": [("a", "G1")]})
        (root / ".guidsForInstaller.all.xml").write_text("<InstallerMetadata>")
        noise = io.StringIO()
        with contextlib.redirect_stderr(noise):
            with self.assertRaises(SystemExit):
                bwp._guid_entries(root)

    def test_a_payload_outside_the_repository_has_no_committed_baseline(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.assertIsNone(
            bwp._committed_guid_entries(pathlib.Path(tmp.name), HERE))


class GuidDifferences(unittest.TestCase):
    """What changed between the committed GUIDs and the ones on disk."""

    def diff(self, before: dict, after: dict):
        return bwp._guid_differences(
            {k: (v, pathlib.PurePosixPath("x")) for k, v in before.items()},
            {k: (v, pathlib.PurePosixPath("x")) for k, v in after.items()})

    def test_nothing_changed(self):
        self.assertEqual(self.diff({"a": "G1"}, {"a": "G1"}), ([], [], []))

    def test_an_id_was_added(self):
        self.assertEqual(self.diff({"a": "G1"}, {"a": "G1", "b": "G2"}),
                         (["b"], [], []))

    def test_an_id_was_lost(self):
        self.assertEqual(self.diff({"a": "G1", "b": "G2"}, {"a": "G1"}),
                         ([], ["b"], []))

    def test_an_id_kept_its_name_and_changed_its_guid(self):
        self.assertEqual(self.diff({"a": "G1"}, {"a": "G9"}), ([], [], ["a"]))

    def test_all_three_at_once(self):
        self.assertEqual(
            self.diff({"a": "G1", "b": "G2"}, {"a": "G9", "c": "G3"}),
            (["c"], ["b"], ["a"]))


class PinnedConfig(unittest.TestCase):
    """The pyoxidizer.bzl edit that picks the embedded CPython."""

    BZL = ('def make_distribution():\n    return %s\n'
           % bwp.DEFAULT_DISTRIBUTION_CALL)

    def test_every_target_triple_has_a_distribution(self):
        self.assertEqual(sorted(bwp.PYTHON_DISTRIBUTIONS), sorted(bwp.TARGET_TRIPLES))

    def test_each_distribution_is_for_its_own_triple(self):
        for triple, (url, sha256) in bwp.PYTHON_DISTRIBUTIONS.items():
            with self.subTest(triple):
                self.assertIn("-%s-" % triple, url)
                self.assertRegex(sha256, r"^[0-9a-f]{64}$")

    def test_the_default_call_is_replaced(self):
        pinned = bwp.pinned_pyoxidizer_config(self.BZL, "x86_64-pc-windows-msvc")
        url, sha256 = bwp.PYTHON_DISTRIBUTIONS["x86_64-pc-windows-msvc"]
        self.assertNotIn("default_python_distribution", pinned)
        self.assertIn('    return PythonDistribution(sha256 = "%s", url = "%s",'
                      ' flavor = "standalone")\n' % (sha256, url), pinned)

    def test_nothing_else_changes(self):
        bzl = "ROOT = CWD + \"/../..\"\n" + self.BZL + "resolve_targets()\n"
        pinned = bwp.pinned_pyoxidizer_config(bzl, "i686-pc-windows-msvc")
        self.assertTrue(pinned.startswith("ROOT = CWD + \"/../..\"\n"))
        self.assertTrue(pinned.endswith("resolve_targets()\n"))

    def test_an_upstream_change_stops_the_build(self):
        for bzl in ('    return default_python_distribution(python_version = "3.10")\n',
                    self.BZL * 2):
            with self.subTest(bzl):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        bwp.pinned_pyoxidizer_config(bzl, "x86_64-pc-windows-msvc")

    def test_the_copy_sits_beside_the_original(self):
        # pyoxidizer.bzl finds the checkout as CWD + "/../..", and CWD is the
        # directory of the config file PyOxidizer was given.
        self.assertEqual(bwp.PINNED_CONFIG.parent, pathlib.Path("rust") / "hgcli")


class MercurialVersion(unittest.TestCase):
    def stage_with(self, text: str | None) -> pathlib.Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        stage = pathlib.Path(tmp.name)
        if text is not None:
            path = stage / "lib" / "mercurial" / "__version__.py"
            path.parent.mkdir(parents=True)
            path.write_text(text, encoding="utf-8")
        return stage

    def test_what_vcs_versioning_writes(self):
        stage = self.stage_with(
            "# file generated by vcs-versioning\nversion: str\n"
            "__version__ = version = '7.0.1'\n"
            "__version_tuple__ = version_tuple = (7, 0, 1)\n")
        self.assertEqual(bwp.mercurial_version(stage), "7.0.1")

    def test_double_quotes(self):
        stage = self.stage_with('__version__ = version = "7.2.2"\n')
        self.assertEqual(bwp.mercurial_version(stage), "7.2.2")

    def test_no_version_file_stops_the_build(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                bwp.mercurial_version(self.stage_with(None))


class FileVersionNumbers(unittest.TestCase):
    def test_numbers(self):
        for version, want in (("7.0.1", (7, 0, 1, 0)),
                              ("7.2.2.post1.dev3+h1234abcd", (7, 2, 2, 0)),
                              ("7.2rc0", (7, 2, 0, 0)),
                              ("7", (7, 0, 0, 0)),
                              ("1.2.3.4.5", (1, 2, 3, 4))):
            with self.subTest(version):
                self.assertEqual(bwp.file_version_numbers(version), want)

    def test_what_a_version_resource_cannot_hold(self):
        for version in ("unknown", "70000.0"):
            with self.subTest(version):
                with contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit):
                        bwp.file_version_numbers(version)


def walk_version_node(blob: bytes, offset: int = 0) -> dict:
    """Parse one VS_VERSIONINFO node, children and all, strictly.

    Written from the layout rc.exe produces rather than from _version_node(),
    and checked against a real rc.exe resource below, so that the two are not
    merely consistent with each other.
    """
    length, value_length, kind = struct.unpack_from("<HHH", blob, offset)
    end = offset + length
    assert end <= len(blob), "node at %d runs past the resource" % offset
    at = offset + 6
    while blob[at:at + 2] != b"\0\0":
        at += 2
    key = blob[offset + 6:at].decode("utf-16-le")
    at += 2
    at += -at % 4
    size = value_length if kind == 0 else value_length * 2
    value = blob[at:at + size]
    at += size
    children = []
    while True:
        at += -at % 4
        if at >= end:
            break
        child = walk_version_node(blob, at)
        assert child["end"] <= end, "%r runs past its parent %r" % (child["key"], key)
        children.append(child)
        at = child["end"]
    return {"key": key, "kind": kind, "value": value, "children": children,
            "end": end}


def version_strings(tree: dict) -> dict:
    [string_info] = [c for c in tree["children"] if c["key"] == "StringFileInfo"]
    [table] = string_info["children"]
    return {c["key"]: c["value"].decode("utf-16-le").rstrip("\0")
            for c in table["children"]}


class VersionResource(unittest.TestCase):
    """The VS_VERSIONINFO stamped on hg.exe, which only Windows can apply."""

    def test_the_walker_reads_a_resource_rc_exe_wrote(self):
        # python39.dll's resource is CPython's own python_ver_rc.h through
        # rc.exe, whichever distribution the payload was built from.
        data = (HERE / "win" / "Mercurial" / "python39.dll").read_bytes()
        start = data.find("VS_VERSION_INFO".encode("utf-16-le")) - 6
        self.assertEqual(start % 4, 0)
        length = struct.unpack_from("<H", data, start)[0]
        tree = walk_version_node(data[start:start + length])
        self.assertEqual([c["key"] for c in tree["children"]],
                         ["StringFileInfo", "VarFileInfo"])
        self.assertTrue(version_strings(tree)["FileVersion"].startswith("3.9."))

    def test_structure(self):
        blob = bwp.version_resource("7.0.1")
        tree = walk_version_node(blob)
        self.assertEqual(tree["end"], len(blob))
        self.assertEqual(tree["key"], "VS_VERSION_INFO")
        self.assertEqual(tree["kind"], 0)
        self.assertEqual([c["key"] for c in tree["children"]],
                         ["StringFileInfo", "VarFileInfo"])

    def test_fixed_file_info(self):
        tree = walk_version_node(bwp.version_resource("7.2.2"))
        fields = struct.unpack("<13I", tree["value"])
        self.assertEqual(fields[0], 0xFEEF04BD)
        # file version, then product version, each as high and low DWORDs
        self.assertEqual(fields[2:6], (7 << 16 | 2, 2 << 16, 7 << 16 | 2, 2 << 16))
        self.assertEqual(fields[9], 1, "VFT_APP")

    def test_strings(self):
        strings = version_strings(walk_version_node(
            bwp.version_resource("7.0.1.post1.dev3+h1234abcd")))
        self.assertEqual(strings["FileVersion"], "7.0.1.post1.dev3+h1234abcd")
        self.assertEqual(strings["ProductVersion"], "7.0.1.post1.dev3+h1234abcd")
        self.assertEqual(strings["OriginalFilename"], "hg.exe")

    def test_the_string_table_and_translation_agree(self):
        tree = walk_version_node(bwp.version_resource("7.0.1"))
        [string_info, var_info] = tree["children"]
        self.assertEqual(string_info["children"][0]["key"], "040904B0")
        [translation] = var_info["children"]
        self.assertEqual(translation["key"], "Translation")
        self.assertEqual(struct.unpack("<HH", translation["value"]), (0x0409, 1200))


class VersionRegressions(unittest.TestCase):
    """Which file-version changes an MSI upgrade refuses to install."""

    def regressions(self, old, new):
        return bwp.version_regressions({"f.dll": old}, {"f.dll": new})

    def test_older_is_refused(self):
        self.assertEqual(self.regressions("3.9.13150.1013", "3.9.6150.1013"),
                         [("f.dll", "3.9.13150.1013", "3.9.6150.1013")])

    def test_compared_as_numbers_not_text(self):
        self.assertEqual(self.regressions("3.9.6150.1013", "3.9.13150.1013"), [])

    def test_losing_the_version_is_refused(self):
        self.assertEqual(self.regressions("6.5.1.0", None),
                         [("f.dll", "6.5.1.0", None)])

    def test_equal_is_allowed(self):
        self.assertEqual(self.regressions("3.9.13150.1013", "3.9.13150.1013"), [])

    def test_newer_is_allowed(self):
        self.assertEqual(self.regressions("6.5.1.0", "7.0.1.0"), [])

    def test_gaining_a_version_is_allowed(self):
        self.assertEqual(self.regressions(None, "7.0.1.0"), [])

    def test_unversioned_on_both_sides_is_allowed(self):
        self.assertEqual(self.regressions(None, None), [])

    def test_only_paths_in_both_count(self):
        self.assertEqual(bwp.version_regressions({"gone.dll": "9.0.0.0"},
                                                 {"new.dll": "1.0.0.0"}), [])


class RunsOnImport(unittest.TestCase):
    """_runs_on_import() on its own, where the recursion is easiest to read."""

    def statements(self, source: str) -> list:
        import ast
        return bwp._runs_on_import(ast.parse(source).body)

    def test_descends_into_a_loop(self):
        self.assertEqual(len(self.statements("for _ in (1,):\n    x = 1\n    y = 2")), 2)

    def test_does_not_descend_into_a_function(self):
        self.assertEqual(self.statements("def f():\n    x = 1"), [])

    def test_keeps_the_statement_itself_when_it_is_not_compound(self):
        self.assertEqual(len(self.statements("x = 1\ny = 2")), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)