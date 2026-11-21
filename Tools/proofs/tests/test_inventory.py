import pathlib
import sys
import unittest

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


if __name__ == "__main__":
    unittest.main()
