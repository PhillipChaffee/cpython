import json
import os
import pathlib
import subprocess
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[3]
FIX = pathlib.Path(__file__).resolve().parent / "fixtures"


def run(cmd, **kw):
    return subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)


class ToolchainTest(unittest.TestCase):
    def test_cbmc_proves_and_refutes(self):
        ok = run(["cbmc", FIX / "smoke.c", "--function", "f", "--signed-overflow-check"])
        bad = run(["cbmc", FIX / "smoke.c", "--function", "g", "--signed-overflow-check"])
        self.assertEqual(ok.returncode, 0)
        self.assertEqual(bad.returncode, 10)  # CBMC: 10 = property violated

    def test_compile_db_covers_core_sources(self):
        path = ROOT / "build/proofs/compile_commands.json"
        if not path.exists():
            self.fail(f"{path} missing; run Tools/proofs/build_db.sh")
        with open(path) as fh:
            db = json.load(fh)
        files = {os.path.relpath(os.path.join(e["directory"], e["file"]), ROOT) for e in db}
        for f in ("Objects/longobject.c", "Python/ceval.c", "Parser/parser.c"):
            self.assertIn(f, files)


if __name__ == "__main__":
    unittest.main()
