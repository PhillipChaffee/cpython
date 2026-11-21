import json
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


def _copy(src, tmp, edit_toml=None, edit_c=None):
    h = pathlib.Path(tmp) / src.name
    shutil.copytree(src, h)
    if edit_toml:
        (h / "proof.toml").write_text(edit_toml((h / "proof.toml").read_text()))
    if edit_c:
        (h / "harness.c").write_text(edit_c((h / "harness.c").read_text()))
    return h


class AuditFixtureTest(unittest.TestCase):
    def audit_copy(self, src, **edits):
        with tempfile.TemporaryDirectory() as tmp:
            h = _copy(src, tmp, **edits)
            return audit.audit(h, out=pathlib.Path(tmp) / "out")

    def test_contradictory_assume_is_rejected(self):
        v = audit.audit(FIX / "vacuous_harness")         # assume(n > 4); assume(n < 2);
        self.assertFalse(v.ok)
        self.assertIn("vacuous", " ".join(v.problems))
        self.assertIsNone(v.audited_tier)

    def test_surviving_mutant_blocks_t2(self):
        v = audit.audit(FIX / "weak_harness")            # declares T2, asserts only r >= 0
        self.assertTrue(v.ok, v.problems)
        self.assertEqual(v.audited_tier, "T1")
        self.assertGreater(v.mutants_total, 0)
        self.assertLess(v.mutants_killed, v.mutants_total)
        self.assertIn("surviving mutant", " ".join(v.problems))

    def test_strong_postcondition_keeps_t2(self):
        v = audit.audit(FIX / "strong_harness")
        self.assertTrue(v.ok, v.problems)
        self.assertEqual(v.audited_tier, "T2", v.problems)
        self.assertGreater(v.mutants_killed, 0)

    def test_unlisted_equivalent_mutant_demotes(self):
        v = self.audit_copy(FIX / "strong_harness",
                            edit_toml=lambda t: t.split("[[equivalent_mutants]]")[0].rstrip() + "\n")
        self.assertEqual(v.audited_tier, "T1")
        self.assertIn("if (s <= 0)", " ".join(v.problems))

    def test_audit_json_written(self):
        v = audit.audit(FIX / "weak_harness")
        data = json.loads((run.OUT / "_fixtures/weak_harness/audit.json").read_text())
        self.assertEqual(data["ok"], v.ok)
        self.assertEqual(data["audited_tier"], "T1")
        for key in ("problems", "stubs", "assumes", "mutants_total", "mutants_killed", "input_hash"):
            self.assertIn(key, data)
        self.assertEqual(data["assumes"], [
            "harness.c:6: __CPROVER_assume(-100 <= a && a <= 100)",
            "harness.c:7: __CPROVER_assume(-100 <= b && b <= 100)"])
        result = json.loads((run.OUT / "_fixtures/weak_harness/result.json").read_text())
        self.assertEqual(data["input_hash"], result["input_hash"])
        self.assertEqual(data["input_hash"], run.input_hash(FIX / "weak_harness"))

    def test_t1_harness_is_not_mutated(self):
        v = self.audit_copy(FIX / "loop_harness_ok", edit_toml=lambda t: t.replace('"T2"', '"T1"'))
        self.assertTrue(v.ok, v.problems)
        self.assertEqual((v.audited_tier, v.mutants_total), ("T1", 0))

    def test_unlisted_model_is_rejected(self):
        models = FIX / "fixture_models"
        with unittest.mock.patch.object(run, "MODELS", models):
            v = audit.audit(FIX / "unlisted_model_harness")
            self.assertFalse(v.ok)
            self.assertIn("unlisted model models/fake_model.c", " ".join(v.problems))
            # Listed (and linked by the runner rather than #included) it is accepted.
            v = self.audit_copy(
                FIX / "unlisted_model_harness",
                edit_toml=lambda t: t.replace("models = []", 'models = ["fake_model"]'),
                edit_c=lambda c: c.replace('#include "fake_model.c"', "int fake_model_value(void);"))
        self.assertTrue(v.ok, v.problems)
        self.assertEqual(v.stubs, ["models/fake_model.c"])

    def test_disabled_check_needs_reason(self):
        def disable(reason):
            return lambda t: (t.replace("disable_checks = []", 'disable_checks = ["--conversion-check"]')
                              .replace('disable_reason = ""', f'disable_reason = "{reason}"'))
        v = self.audit_copy(FIX / "loop_harness_ok", edit_toml=disable(""))
        self.assertFalse(v.ok)
        self.assertIn("disable_reason", " ".join(v.problems))
        v = self.audit_copy(FIX / "loop_harness_ok",
                            edit_toml=lambda t: disable("sum() is int only")(t).replace('"T2"', '"T1"'))
        self.assertTrue(v.ok, v.problems)

    def test_run_without_unwinding_assertions_is_rejected(self):
        cfg = {"tier": "T1", "unwind": 5}
        res = run.Result(status="pass", tier="T1",
                         cbmc_flags=["--unwind", "5", *[c for c in run.CHECKS
                                                         if c != "--unwinding-assertions"]])
        self.assertIn("--unwinding-assertions", " ".join(audit.check_flags(cfg, res)))

    def test_unreachable_harness_assertion_is_rejected(self):
        v = audit.audit(FIX / "unreachable_assert_harness")
        self.assertFalse(v.ok)
        self.assertEqual([p for p in v.problems if "unreachable" in p],
                         ["harness.c:12: __CPROVER_assert is unreachable (vacuous)"])

    def test_bodyless_callee_is_rejected_unless_acknowledged(self):
        v = audit.audit(FIX / "bodyless_harness")
        self.assertFalse(v.ok)
        text = " ".join(v.problems)
        self.assertIn("ext_lookup", text)
        self.assertNotIn("never_called", text)      # only on an infeasible path
        ack = lambda t: t + 'havoc_callees = ["ext_lookup"]\nhavoc_reason = "test"\n'
        v = self.audit_copy(FIX / "bodyless_harness", edit_toml=ack)
        self.assertTrue(v.ok, v.problems)
        self.assertEqual(v.stubs, ["havoc:ext_lookup"])
        v = self.audit_copy(FIX / "bodyless_harness",
                            edit_toml=lambda t: t + 'havoc_callees = ["ext_lookup"]\n')
        self.assertFalse(v.ok)
        self.assertIn("havoc_reason", " ".join(v.problems))

    def test_t3_is_demoted_until_contract_mode_exists(self):
        v = self.audit_copy(FIX / "weak_harness",          # no loops: runs without unwind
                            edit_toml=lambda t: t.replace('"T2"', '"T3"').replace("unwind = 1\n", ""))
        self.assertTrue(v.ok, v.problems)
        self.assertEqual(v.audited_tier, "T1")
        self.assertIn("not implemented", " ".join(v.problems))


class InputHashTest(unittest.TestCase):
    def test_editing_harness_changes_input_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = _copy(FIX / "loop_harness_ok", tmp)
            before = run.input_hash(h)
            self.assertEqual(before, run.input_hash(h))
            v = audit.audit(h, out=pathlib.Path(tmp) / "out")
            self.assertEqual(v.input_hash, before)
            (h / "harness.c").write_text((h / "harness.c").read_text() + "\n/* edited */\n")
            after = run.input_hash(h)
            self.assertNotEqual(before, after)
            # The stale result.json is not reused: the audit re-runs the harness.
            v = audit.audit(h, out=pathlib.Path(tmp) / "out")
            self.assertEqual(v.input_hash, after)
            res = json.loads((pathlib.Path(tmp) / "out/_fixtures/loop_harness_ok/result.json").read_text())
            self.assertEqual(res["input_hash"], after)


class MutantSelectionTest(unittest.TestCase):
    def _mutants(self):
        src = FIX / "weak.c"
        return audit.generate_mutants(src, {3, 4, 5, 6})

    def test_mutants_only_touch_given_lines(self):
        ms = self._mutants()
        self.assertTrue(ms)
        self.assertEqual({m.line for m in ms} - {3, 4, 5, 6}, set())

    def test_mutant_sampling_is_deterministic(self):
        ms = self._mutants()
        a = [m.diff for m in audit.order_mutants(ms)]
        b = [m.diff for m in audit.order_mutants(list(reversed(self._mutants())))]
        self.assertEqual(a, b)
        self.assertNotEqual(a, [m.diff for m in sorted(ms, key=lambda m: (m.line, m.mutated))])

    def test_non_compiling_mutants_are_discarded(self):
        hdir = FIX / "weak_harness"
        cfg = run._read_config(hdir)
        src = (ROOT / cfg["source"]).resolve()
        lines = src.read_text().splitlines(keepends=True)
        good = audit.mutant_from(lines, src.read_text().replace("a + b", "a - b"))
        bad = audit.mutant_from(lines, src.read_text().replace("a + b", "a +* b"))
        with tempfile.TemporaryDirectory() as tmp:
            sel, discarded = audit.select_mutants(hdir, cfg, src, [bad, good], 5,
                                                  pathlib.Path(tmp), jobs=2)
        self.assertEqual([m.diff for m, _ in sel], [good.diff])
        self.assertEqual(discarded, 1)

    def test_mutant_limit(self):
        hdir = FIX / "weak_harness"
        cfg = run._read_config(hdir)
        src = (ROOT / cfg["source"]).resolve()
        with tempfile.TemporaryDirectory() as tmp:
            sel, _ = audit.select_mutants(hdir, cfg, src, audit.order_mutants(self._mutants()), 2,
                                          pathlib.Path(tmp), jobs=2)
        self.assertEqual(len(sel), 2)


@unittest.skipIf(SLOW, "PROOFS_SKIP_SLOW=1")
class RealHarnessAuditTest(unittest.TestCase):
    def test_v_iadd_keeps_t2(self):
        v = audit.audit(HARNESSES / "Objects/longobject.c/v_iadd")
        self.assertTrue(v.ok, v.problems)
        self.assertEqual(v.audited_tier, "T2", v.problems)
        self.assertGreater(v.mutants_total, 0)
        # Only the listed equivalent mutant may survive.
        self.assertEqual([p for p in v.problems if "not killed" in p or "surviving" in p], [])


if __name__ == "__main__":
    unittest.main()
