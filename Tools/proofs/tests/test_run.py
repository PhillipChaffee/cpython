import json
import os
import pathlib
import shutil
import sys
import tempfile
import unittest

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FIX = HERE / "fixtures"
HARNESSES = ROOT / "Tools/proofs/harnesses"
sys.path.insert(0, str(HERE.parent))

import run  # noqa: E402


class RunFixtureTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ok = run.run_harness(FIX / "loop_harness_ok")

    def test_passing_harness_reports_covered_lines(self):
        r = self.ok
        self.assertEqual(r.status, "pass", r.detail)
        self.assertEqual(r.covered["loop.c"], [2, 3, 4, 5])

    def test_harness_file_not_in_covered(self):
        self.assertEqual(list(self.ok.covered), ["loop.c"])

    def test_result_fields(self):
        r = self.ok
        self.assertEqual(r.tier, "T2")
        self.assertEqual(r.assumes, 2)
        self.assertEqual(r.stubs, [])
        self.assertGreater(r.seconds, 0)
        # only properties of reached functions (harness, sum) are counted
        self.assertGreater(r.properties_checked, 0)
        self.assertLess(r.properties_checked, 40)

    def test_result_json_written(self):
        path = run.OUT / "_fixtures/loop_harness_ok/result.json"
        data = json.loads(path.read_text())
        self.assertEqual(data["status"], "pass")
        self.assertEqual(data["covered"], {"loop.c": [2, 3, 4, 5]})

    def test_insufficient_unwind_fails(self):
        r = run.run_harness(FIX / "loop_harness_unwind2")
        self.assertEqual(r.status, "fail")
        self.assertIn("unwinding assertion", r.detail)
        self.assertEqual(r.covered, {})

    def test_disable_checks_needs_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = pathlib.Path(tmp) / "h"
            shutil.copytree(FIX / "loop_harness_ok", h)
            toml = (h / "proof.toml").read_text()
            toml = toml.replace('disable_checks = []', 'disable_checks = ["--conversion-check"]')
            (h / "proof.toml").write_text(toml)
            r = run.run_harness(h, out=pathlib.Path(tmp) / "out")
            self.assertEqual(r.status, "error")
            self.assertIn("disable_reason", r.detail)

    def test_contradictory_assumptions_fail(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = pathlib.Path(tmp) / "h"
            shutil.copytree(FIX / "loop_harness_ok", h)
            c = (h / "harness.c").read_text()
            c = c.replace("int s = sum(a, n);", "__CPROVER_assume(n > 4);\n    int s = sum(a, n);")
            (h / "harness.c").write_text(c)
            r = run.run_harness(h, out=pathlib.Path(tmp) / "out")
            self.assertEqual(r.status, "fail")
            self.assertIn("vacuous", r.detail)

    def test_disable_checks_outside_list_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = pathlib.Path(tmp) / "h"
            shutil.copytree(FIX / "loop_harness_ok", h)
            toml = (h / "proof.toml").read_text()
            toml = toml.replace('disable_checks = []', 'disable_checks = ["--bounds-check"]')
            toml = toml.replace('disable_reason = ""', 'disable_reason = "x"')
            (h / "proof.toml").write_text(toml)
            r = run.run_harness(h, out=pathlib.Path(tmp) / "out")
            self.assertEqual(r.status, "error")


class CompileDbTest(unittest.TestCase):
    def _fake_build(self, tmp, db_age, header_age):
        b = pathlib.Path(tmp)
        (b / "compile_commands.json").write_text("[]")
        (b / "pyconfig.h").write_text("")
        (b / "Makefile").write_text("")
        now = 1_700_000_000
        os.utime(b / "compile_commands.json", (now - db_age, now - db_age))
        os.utime(b / "pyconfig.h", (now - header_age, now - header_age))
        os.utime(b / "Makefile", (now - 1000, now - 1000))
        return b

    def test_stale_compile_db_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._fake_build(tmp, db_age=100, header_age=10)
            with self.assertRaisesRegex(run.StaleBuild, "build_db.sh"):
                run.load_compile_db(b)

    def test_fresh_compile_db_loads(self):
        with tempfile.TemporaryDirectory() as tmp:
            b = self._fake_build(tmp, db_age=10, header_age=100)
            self.assertEqual(run.load_compile_db(b), {})


class NewHarnessTest(unittest.TestCase):
    def test_creates_t1_skeleton(self):
        import new_harness
        with tempfile.TemporaryDirectory() as tmp:
            d = new_harness.create("Objects/longobject.c", "v_iadd", base=pathlib.Path(tmp))
            self.assertEqual(d, pathlib.Path(tmp) / "Objects/longobject.c/v_iadd")
            toml = (d / "proof.toml").read_text()
            self.assertIn('tier = "T1"', toml)
            self.assertIn('function = "v_iadd"', toml)
            c = (d / "harness.c").read_text()
            self.assertIn('#include "Objects/longobject.c"', c)
            self.assertIn("void harness(void)", c)
            with self.assertRaises(FileExistsError):
                new_harness.create("Objects/longobject.c", "v_iadd", base=pathlib.Path(tmp))


class RealHarnessTest(unittest.TestCase):
    def test_real_harnesses_pass(self):
        for h in ("Objects/bytes_methods.c/_Py_bytes_isalnum",
                  "Python/mystrtoul.c/PyOS_strtoul",
                  "Objects/longobject.c/v_iadd"):
            with self.subTest(h):
                r = run.run_harness(HARNESSES / h)
                self.assertEqual(r.status, "pass", f"{h}: {r.detail}")
                src = h.rsplit("/", 1)[0]
                self.assertTrue(r.covered.get(src), f"{h}: no lines covered in {src}")


if __name__ == "__main__":
    unittest.main()
