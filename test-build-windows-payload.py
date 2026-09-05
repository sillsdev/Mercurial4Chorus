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
KEPT = "lib/mercurial/probe.py"


class StageMixin:
    """A two-file staging tree: one package trimmed away, one module kept."""

    def stage_with(self, source: str, module: str = KEPT) -> pathlib.Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        stage = pathlib.Path(tmp.name)
        removed = stage / "lib" / REMOVED
        removed.mkdir(parents=True)
        (removed / "__init__.py").write_text("")
        kept = stage / module
        kept.parent.mkdir(parents=True, exist_ok=True)
        kept.write_text(source + "\n")
        return stage

    def check(self, source: str, module: str = KEPT) -> bool:
        """Does check_python_imports() reject a payload built from *source*?"""
        stage = self.stage_with(source, module)
        noise = io.StringIO()
        try:
            with contextlib.redirect_stdout(noise), contextlib.redirect_stderr(noise):
                bwp.check_python_imports(stage, True, True)
        except SystemExit:
            return True
        return False

    def assertRejected(self, source: str, module: str = KEPT) -> None:
        self.assertTrue(self.check(source, module), "should have been rejected")

    def assertAccepted(self, source: str, module: str = KEPT) -> None:
        self.assertFalse(self.check(source, module), "should have been accepted")


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