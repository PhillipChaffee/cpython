import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FIX = HERE / "fixtures"
sys.path.insert(0, str(HERE.parent))

import inventory  # noqa: E402

DB = ROOT / "build/proofs/compile_commands.json"
EMPTY_SCOPE = inventory.Scope(())


class InventoryTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inv_fixture = inventory.build_from_sources([FIX / "inv.c"], scope=EMPTY_SCOPE)

    def test_counts_executable_lines_only(self):
        # inv.c has 2 functions; comments, blank lines and declarations are not executable
        inv = self.inv_fixture
        self.assertEqual(sorted(inv.files["inv.c"].functions), ["add", "clamp"])
        self.assertEqual(inv.files["inv.c"].functions["add"].lines, [3])
        self.assertEqual(inv.files["inv.c"].functions["clamp"].lines, [7, 8, 9, 10])

    @unittest.skipUnless(DB.exists(), f"{DB} missing; run Tools/proofs/build_db.sh")
    def test_scope_excludes_vendored(self):
        inv = inventory.build(DB, ROOT / "Tools/proofs/scope.toml")
        self.assertFalse(any(p.startswith("Modules/_hacl/") for p in inv.files))
        self.assertIn("Modules/_hacl", " ".join(inv.excluded))
        self.assertIn("Objects/longobject.c", inv.files)

    def test_ifdef_off_code_is_absent(self):
        # a function inside `#if 0` / an undefined macro must not be in the denominator
        self.assertNotIn("dead_fn", self.inv_fixture.files["inv.c"].functions)

    def test_static_flag(self):
        inv = inventory.build_from_sources([FIX / "inv_static.c"], scope=EMPTY_SCOPE)
        fns = inv.files["inv_static.c"].functions
        self.assertTrue(fns["helper"].static)
        self.assertFalse(fns["api"].static)


def write_db(tmp, entries):
    db = pathlib.Path(tmp) / "cc.json"
    db.write_text(json.dumps([
        {"directory": str(FIX), "file": name, "arguments": ["gcc", *flags, "-c", name]}
        for name, flags in entries]))
    return db


class RobustnessTest(unittest.TestCase):
    def test_same_source_with_different_flags_is_unioned(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = write_db(tmp, [("inv_flags.c", ["-DVARIANT_A"]), ("inv_flags.c", ["-DVARIANT_B"])])
            inv = inventory.build(db, EMPTY_SCOPE, cache_dir=None, root=FIX)
        self.assertEqual(sorted(inv.files["inv_flags.c"].functions), ["variant_a", "variant_b"])

    def test_errors_are_reported_and_never_cached(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = write_db(tmp, [("inv_bad.c", []), ("inv.c", [])])
            cache = pathlib.Path(tmp) / "cache"
            inv = inventory.build(db, EMPTY_SCOPE, cache_dir=cache, root=FIX)
            self.assertIn("inv_bad.c", inv.errors)
            self.assertIn("add", inv.files["inv.c"].functions)
            self.assertEqual(len(list(cache.glob("*.json"))), 1)  # inv.c only

    def test_exception_in_one_file_does_not_abort_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = write_db(tmp, [("inv.c", [])])
            with mock.patch.object(inventory, "analyse_unit", side_effect=StopIteration):
                inv = inventory.build(db, EMPTY_SCOPE, cache_dir=None, root=FIX)
        self.assertIn("inv.c", inv.errors)

    def test_cache_key_includes_cbmc_version(self):
        src = FIX / "inv.c"
        with mock.patch.object(inventory, "cbmc_version", return_value="CBMC 1"):
            a = inventory._cache_path("c", src, [])
        with mock.patch.object(inventory, "cbmc_version", return_value="CBMC 2"):
            b = inventory._cache_path("c", src, [])
        self.assertNotEqual(a, b)

    def test_cli_exit_status_and_no_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = write_db(tmp, [("inv_bad.c", []), ("inv.c", [])])
            out = pathlib.Path(tmp) / "inv.json"
            scope = pathlib.Path(tmp) / "scope.toml"
            scope.write_text("exclude = []\n")
            base = ["--db", str(db), "--scope", str(scope),
                    "--out", str(out), "--no-cache"]
            with contextlib.redirect_stdout(io.StringIO()) as so, \
                    contextlib.redirect_stderr(io.StringIO()) as se:
                self.assertEqual(inventory.main(base), 1)
                self.assertEqual(inventory.main(base + ["--allow-errors"]), 0)
            self.assertIn('"errors": 1', so.getvalue())
            self.assertIn("NOT in these totals", se.getvalue())
            self.assertIn("Tools/proofs/tests/fixtures/inv_bad.c", json.loads(out.read_text())["errors"])

    def test_relativise_keeps_dotdot_prefixed_names(self):
        raw = {str(ROOT / "..hidden/x.h"): {}, str(ROOT.parent / "outside.h"): {},
               str(ROOT / "build/y.h"): {}}
        self.assertEqual(list(inventory.relativise(raw, ROOT)), ["..hidden/x.h"])


if __name__ == "__main__":
    unittest.main()
