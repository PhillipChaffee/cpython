import os
import pathlib
import shutil
import sys
import tempfile
import unittest
import unittest.mock

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[2]
FIX = HERE / "fixtures"
HARNESSES = ROOT / "Tools/proofs/harnesses"
sys.path.insert(0, str(HERE.parent))

import audit  # noqa: E402
import run  # noqa: E402

SLOW = os.environ.get("PROOFS_SKIP_SLOW") == "1"
CONTRACTS = FIX / "fixture_contracts"

PILOT = {
    "Objects/longobject.c/x_add": "T2",
    "Objects/longobject.c/x_sub": "T2",
    "Objects/longobject.c/v_isub": "T2",
    "Objects/longobject.c/v_lshift": "T2",
    "Objects/longobject.c/v_rshift": "T2",
    "Objects/longobject.c/inplace_divrem1": "T2",
    "Objects/bytes_methods.c/_Py_bytes_lower": "T2",
    "Python/pyhash.c/Py_HashBuffer": "T1",
}


def _copy(src, tmp, edit_c=None, edit_toml=None):
    h = pathlib.Path(tmp) / src.name
    shutil.copytree(src, h)
    if edit_c:
        (h / "harness.c").write_text(edit_c((h / "harness.c").read_text()))
    if edit_toml:
        (h / "proof.toml").write_text(edit_toml((h / "proof.toml").read_text()))
    return h


class ModelTest(unittest.TestCase):
    def test_model_alloc_can_fail(self):
        r = run.run_harness(FIX / "alloc_never_null")
        self.assertEqual(r.status, "fail", r.detail)
        for name in ("PyMem_Malloc", "PyMem_Calloc", "PyMem_Realloc", "PyMem_RawMalloc",
                     "PyObject_Malloc", "PyObject_Calloc", "PyObject_Realloc",
                     "_PyObject_GC_New", "_PyObject_GC_NewVar"):
            self.assertIn(f"{name} never fails", r.detail)

    def test_model_objects_are_well_formed(self):
        r = run.run_harness(FIX / "model_long_wf")
        self.assertEqual(r.status, "pass", r.detail)
        # Every branch of the harness is reachable: each kind of object occurs.
        v = audit.audit(FIX / "model_long_wf")
        self.assertTrue(v.ok, v.problems)

    def test_generic_call_null_implies_error_set(self):
        r = run.run_harness(FIX / "generic_call_harness")
        self.assertEqual(r.status, "pass", r.detail)
        v = audit.audit(FIX / "generic_call_harness")
        self.assertTrue(v.ok, v.problems)              # both outcomes are reachable

    def test_model_files_list_their_header(self):
        # models.h is the entry "models"; a harness that includes it without
        # listing it is refused like any other unlisted model file.
        with tempfile.TemporaryDirectory() as tmp:
            h = _copy(FIX / "generic_call_harness", tmp,
                      edit_toml=lambda t: t.replace('"models", ', ""))
            v = audit.audit(h, out=pathlib.Path(tmp) / "out")
        self.assertFalse(v.ok)
        self.assertIn("unlisted model models/models.h", " ".join(v.problems))


class AllocationFailureTest(unittest.TestCase):
    """Ruling: CBMC's malloc never fails by default."""

    def test_malloc_may_fail_is_a_fixed_check(self):
        for flag in ("--malloc-may-fail", "--malloc-fail-null"):
            self.assertIn(flag, run.CHECKS)
            self.assertNotIn(flag, run.DISABLEABLE)

    def test_null_branch_is_reachable(self):
        r = run.run_harness(FIX / "malloc_null_path_harness")
        self.assertEqual(r.status, "pass", r.detail)
        self.assertIn("--malloc-may-fail", r.cbmc_flags)
        v = audit.audit(FIX / "malloc_null_path_harness")
        self.assertTrue(v.ok, v.problems)              # "the allocation can fail" is reachable

    def test_malloc_can_fail_in_contract_mode(self):
        # goto-instrument links CBMC's library, so it needs the flags too.
        edit = lambda c: c.replace("add_into(&x, v);",
                                   "add_into(&x, v);\n    int *p = malloc(4);\n"
                                   "    __CPROVER_assert(p != 0, \"malloc never fails\");")
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch.object(run, "CONTRACTS", CONTRACTS):
            h = _copy(FIX / "contract_enforce_harness", tmp,
                      edit_c=lambda c: "#include <stdlib.h>\n" + edit(c))
            r = run.run_harness(h, out=pathlib.Path(tmp) / "out")
        self.assertEqual(r.status, "fail", r.detail)
        self.assertIn("malloc never fails", r.detail)


class ContractTest(unittest.TestCase):
    def setUp(self):
        patch = unittest.mock.patch.object(run, "CONTRACTS", CONTRACTS)
        patch.start()
        self.addCleanup(patch.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = pathlib.Path(self.tmp.name) / "out"

    def test_contract_is_enforced(self):
        r = run.run_harness(FIX / "contract_enforce_harness", out=self.out)
        self.assertEqual(r.status, "pass", r.detail)

    def test_wrong_contract_fails_enforcement(self):
        with tempfile.TemporaryDirectory() as tmp:
            contracts = pathlib.Path(tmp) / "contracts"
            shutil.copytree(CONTRACTS, contracts)
            h = contracts / "add_into.h"
            h.write_text(h.read_text().replace("__CPROVER_old(*x) + v", "__CPROVER_old(*x) + v + 1"))
            with unittest.mock.patch.object(run, "CONTRACTS", contracts):
                r = run.run_harness(FIX / "contract_enforce_harness", out=self.out)
        self.assertEqual(r.status, "fail", r.detail)
        self.assertIn("ensures", r.detail)

    def test_missing_contract_is_an_error(self):
        # goto-instrument would derive an empty contract (assigns nothing).
        with tempfile.TemporaryDirectory() as tmp:
            h = _copy(FIX / "contract_enforce_harness", tmp,
                      edit_c=lambda c: c.replace('#include "add_into.h"\n', ""))
            r = run.run_harness(h, out=self.out)
        self.assertEqual(r.status, "error")
        self.assertIn("no contract clauses for add_into", r.detail)

    def test_replaced_contract_requires_are_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = _copy(FIX / "contract_caller_harness", tmp,
                      edit_c=lambda c: c.replace(" && x <= 50", ""))
            r = run.run_harness(h, out=self.out)
        self.assertEqual(r.status, "fail", r.detail)
        self.assertIn("requires", r.detail)

    def test_replacement_with_enforced_contract_is_accepted(self):
        enforce = audit.audit(FIX / "contract_enforce_harness", out=self.out)
        self.assertTrue(enforce.ok, enforce.problems)
        self.assertIn("contracts/add_into.h", enforce.contract_hashes)
        v = audit.audit(FIX / "contract_caller_harness", out=self.out)
        self.assertTrue(v.ok, v.problems)
        self.assertIn("contracts/add_into.h", " ".join(v.assumes))   # the ensures, assumed

    def test_contract_without_enforcing_harness_is_rejected(self):
        v = audit.audit(FIX / "orphan_contract", out=self.out)
        self.assertFalse(v.ok)
        self.assertIn("no enforcing harness", " ".join(v.problems))

    def test_contract_changed_since_enforcement_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            contracts = pathlib.Path(tmp) / "contracts"
            shutil.copytree(CONTRACTS, contracts)
            with unittest.mock.patch.object(run, "CONTRACTS", contracts):
                self.assertTrue(audit.audit(FIX / "contract_enforce_harness", out=self.out).ok)
                h = contracts / "add_into.h"
                h.write_text(h.read_text() + "/* changed */\n")
                v = audit.audit(FIX / "contract_caller_harness", out=self.out)
        self.assertFalse(v.ok)
        self.assertIn("no enforcing harness", " ".join(v.problems))

    def test_contract_clauses_outside_contracts_are_refused(self):
        # goto-cc refuses a second contract for add_into, but the harness
        # could declare its own instead of including the enforced header.
        clause = ("static int add_into(int *x, int v)\n"
                  "__CPROVER_assigns(*x) __CPROVER_ensures(*x == __CPROVER_old(*x) + v)\n"
                  "__CPROVER_ensures(__CPROVER_return_value == *x);\n")
        self.assertTrue(audit.audit(FIX / "contract_enforce_harness", out=self.out).ok)
        with tempfile.TemporaryDirectory() as tmp:
            h = _copy(FIX / "contract_caller_harness", tmp,
                      edit_c=lambda c: c.replace('#include "add_into.h"\n', clause))
            self.assertEqual(run.run_harness(h, out=self.out).status, "pass")
            v = audit.audit(h, out=self.out)
        self.assertFalse(v.ok)
        self.assertIn("contract clauses", " ".join(v.problems))

    def test_enforce_contract_must_name_the_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = _copy(FIX / "contract_enforce_harness", tmp,
                      edit_toml=lambda t: t.replace('enforce_contract = "add_into"',
                                                    'enforce_contract = "add_three"'))
            r = run.run_harness(h, out=self.out)
        self.assertEqual(r.status, "error")
        self.assertIn("enforce_contract must name the function under proof", r.detail)


@unittest.skipIf(SLOW, "PROOFS_SKIP_SLOW=1")
class PilotTest(unittest.TestCase):
    def test_pilot_harnesses_pass_audit(self):
        for rel, tier in PILOT.items():
            with self.subTest(rel):
                v = audit.audit(HARNESSES / rel)
                self.assertTrue(v.ok, v.problems)
                self.assertEqual(v.audited_tier, tier, v.problems)

    def test_longobject_contracts_replace_in_a_caller(self):
        # End to end: v_iadd and v_isub enforce contracts/longobject.h, then
        # a caller replaces both calls by those contracts.
        for name in ("v_iadd", "v_isub"):
            v = audit.audit(HARNESSES / "Objects/longobject.c" / name)
            self.assertTrue(v.ok, v.problems)
            self.assertIn("contracts/longobject.h", v.contract_hashes)
        v = audit.audit(FIX / "longobject_contract_caller")
        self.assertTrue(v.ok, v.problems)
        self.assertIn("contracts/longobject.h", " ".join(v.assumes))

    def test_v_iadd_v_isub_contracts_are_enforced(self):
        for name in ("v_iadd", "v_isub"):
            cfg = run._read_config(HARNESSES / "Objects/longobject.c" / name)
            self.assertEqual(cfg.get("enforce_contract"), name)
            self.assertIn("longobject", cfg["contracts"])


if __name__ == "__main__":
    unittest.main()
