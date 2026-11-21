import dataclasses
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

HERE = pathlib.Path(__file__).resolve().parent
PROOFS = HERE.parent
sys.path.insert(0, str(PROOFS))

import coverage  # noqa: E402
import inventory  # noqa: E402

F = inventory.Function


def _inv(files, errors=None):
    return inventory.Inventory(
        files={p: inventory.FileInventory(fs) for p, fs in files.items()},
        errors=errors or {})


def _rec(name, status="pass", declared="T1", audited_tier="T1", audited=True,
         covered=None, audit_ok=True):
    return coverage.HarnessRecord(
        harness=name, status=status, declared_tier=declared,
        audited_tier=audited_tier, audited=audited, audit_ok=audit_ok,
        covered=covered or {})


# inv.c: add 1-4, clamp 5-7, sub 8-10
INV10 = _inv({"inv.c": {"add": F([1, 2, 3, 4], False),
                        "clamp": F([5, 6, 7], True),
                        "sub": F([8, 9, 10], False)}})
A_T1 = _rec("inv.c/add", covered={"inv.c": [1, 2, 3, 4]})
B_T2 = _rec("inv.c/mid", declared="T2", audited_tier="T2",
            covered={"inv.c": [3, 4, 5, 6]})
A_FAILED = _rec("inv.c/failed", status="fail", covered={"inv.c": [1, 2]})
B_UNAUDITED = _rec("inv.c/unaudited", audited=False, audited_tier=None,
                   covered={"inv.c": [1, 2]})
B_BADAUDIT = _rec("inv.c/badaudit", audit_ok=False, covered={"inv.c": [1, 2]})
COVERS_LINE_99 = _rec("inv.c/far", covered={"inv.c": [99], "other.c": [1]})
COVERS_SUB = _rec("inv.c/sub", covered={"inv.c": [8, 9, 10]})


def _write_json(d, name, data):
    p = pathlib.Path(d) / name
    p.write_text(json.dumps(data))
    return p


class MergeTest(unittest.TestCase):
    def test_percentages_are_exact(self):
        # harness A (T1) covers 1-4, harness B (T2) covers 3-6
        rep = coverage.merge(INV10, [A_T1, B_T2])
        self.assertEqual((rep.t1, rep.t2, rep.t3, rep.total_lines), (6, 4, 0, 10))

    def test_t3_counts_as_t2_and_t1(self):
        t3 = _rec("h", declared="T3", audited_tier="T3", covered={"inv.c": [1, 2]})
        rep = coverage.merge(INV10, [t3])
        self.assertEqual((rep.t1, rep.t2, rep.t3), (2, 2, 2))

    def test_tier_without_assertions_is_t1(self):
        rep = coverage.merge(INV10, [dataclasses.replace(B_T2, audited_tier="T1")])
        self.assertEqual(rep.t2, 0)
        self.assertEqual(rep.t1, 4)

    def test_failed_or_unaudited_harness_counts_zero(self):
        rep = coverage.merge(INV10, [A_FAILED, B_UNAUDITED, B_BADAUDIT])
        self.assertEqual(rep.t1, 0)
        self.assertEqual(rep.harnesses_counted, 0)
        self.assertEqual(len(rep.skipped), 3)

    def test_lines_outside_inventory_are_ignored(self):
        self.assertEqual(coverage.merge(INV10, [COVERS_LINE_99]).t1, 0)

    def test_unaudited_provisional_is_t1_only(self):
        t2 = dataclasses.replace(B_UNAUDITED, declared_tier="T3")
        rep = coverage.merge(INV10, [t2, A_FAILED, B_BADAUDIT], include_unaudited=True)
        self.assertEqual((rep.t1, rep.t2, rep.t3), (2, 0, 0))
        self.assertTrue(rep.provisional)

    def test_shared_line_counts_once(self):
        inv = _inv({"a.c": {"f": F([1, 2], False), "g": F([2, 3], False)}})
        rep = coverage.merge(inv, [_rec("h", covered={"a.c": [2]})])
        self.assertEqual((rep.total_lines, rep.t1), (3, 1))
        self.assertEqual(rep.functions_t1, 2)

    def test_function_counts(self):
        rep = coverage.merge(INV10, [A_T1, B_T2])
        self.assertEqual(rep.functions_total, 3)
        self.assertEqual((rep.functions_t1, rep.functions_t2, rep.functions_t3), (2, 2, 0))
        # add fully T1; clamp has 5,6 of 7; sub untouched
        self.assertEqual((rep.functions_full_t1, rep.functions_full_t2), (1, 0))

    def test_by_dir_and_by_file(self):
        inv = _inv({"Objects/a.c": {"f": F([1, 2], False)},
                    "Python/b.c": {"g": F([1], False)},
                    "top.c": {"h": F([1], False)}})
        rep = coverage.merge(inv, [_rec("h", covered={"Objects/a.c": [1]})])
        self.assertEqual(rep.by_dir["Objects"], {"total": 2, "t1": 1, "t2": 0, "t3": 0})
        self.assertEqual(rep.by_dir["Python"]["t1"], 0)
        self.assertIn(".", rep.by_dir)
        self.assertEqual(rep.by_file["Objects/a.c"]["t1"], 1)

    def test_errors_are_not_in_denominator(self):
        inv = _inv({"inv.c": {"add": F([1], False)}}, errors={"bad.c": "goto-cc died"})
        rep = coverage.merge(inv, [])
        self.assertEqual(rep.not_in_denominator, {"bad.c": "goto-cc died"})
        self.assertEqual(rep.to_json()["not_in_denominator"], {"bad.c": "goto-cc died"})

    def test_accepts_inventory_json_dict(self):
        rep = coverage.merge(INV10.to_json(), [A_T1])
        self.assertEqual(rep.t1, 4)


class RenderTest(unittest.TestCase):
    def setUp(self):
        self.rep = coverage.merge(INV10, [COVERS_SUB])

    def test_unreached_lists_every_uncovered_function_with_reason(self):
        text = coverage.render_unreached(self.rep, reasons={"clamp": "unwind-infeasible"})
        self.assertIn("inv.c:clamp — unwind-infeasible", text)
        self.assertIn("inv.c:add — no-harness", text)
        self.assertNotIn("inv.c:sub", text)

    def test_unreached_reasons_by_path_and_star(self):
        reasons = {"inv.c": {"clamp": "inline-asm"}, "other.c": {"*": "platform-ifdef"}}
        text = coverage.render_unreached(self.rep, reasons=reasons)
        self.assertIn("inv.c:clamp — inline-asm", text)
        self.assertIn("inv.c:add — no-harness", text)
        text = coverage.render_unreached(
            self.rep, reasons={"inv.c": {"*": "os-behaviour", "add": "needs-contract"}})
        self.assertIn("inv.c:add — needs-contract", text)
        self.assertIn("inv.c:clamp — os-behaviour", text)

    def test_unreached_rejects_unknown_reason(self):
        with self.assertRaises(ValueError):
            coverage.render_unreached(self.rep, reasons={"clamp": "lazy"})

    def test_unreached_starts_with_not_in_denominator(self):
        inv = _inv({"inv.c": {"add": F([1], False)}}, errors={"bad.c": "goto-cc died"})
        text = coverage.render_unreached(coverage.merge(inv, []))
        self.assertLess(text.index("bad.c"), text.index("inv.c:add"))
        self.assertIn("size unknown, uncovered", text)
        self.assertIn("goto-cc died", text)

    def test_unreached_groups_by_file_with_count(self):
        text = coverage.render_unreached(self.rep)
        self.assertIn("inv.c (2)", text)

    def test_lcov_per_tier(self):
        rep = coverage.merge(INV10, [A_T1, B_T2])
        t2 = coverage.render_lcov(rep, "T2", root=pathlib.Path("/src"))
        self.assertIn("SF:/src/inv.c", t2)
        self.assertIn("DA:3,1", t2)
        self.assertIn("DA:2,0", t2)
        self.assertIn("LF:10", t2)
        self.assertIn("LH:4", t2)
        self.assertTrue(t2.rstrip().endswith("end_of_record"))

    def test_file_report_lists_uncovered_first(self):
        rep = coverage.merge(INV10, [COVERS_SUB])
        lines = coverage.render_file(rep, "inv.c").splitlines()
        self.assertIn("add", lines[1])
        self.assertIn("0/4", lines[1])
        self.assertIn("sub", lines[-1])
        self.assertIn("3/3", lines[-1])
        self.assertIn("T1", lines[-1])

    def test_summary_mentions_counts(self):
        inv = _inv({"inv.c": {"add": F([1], False)}}, errors={"bad.c": "x"})
        text = coverage.render_summary(coverage.merge(inv, [A_FAILED]))
        self.assertIn("not in denominator: 1", text)
        self.assertIn("0.00%", text)
        self.assertIn("harnesses counted: 0", text)


class ReasonsFileTest(unittest.TestCase):
    def test_load(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "r.toml"
            p.write_text('["a.c"]\nf = "inline-asm"\n"*" = "os-behaviour"\n')
            self.assertEqual(coverage.load_reasons(p),
                             {"a.c": {"f": "inline-asm", "*": "os-behaviour"}})
            self.assertEqual(coverage.load_reasons(pathlib.Path(d) / "none.toml"), {})

    def test_load_rejects_unknown_code(self):
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d) / "r.toml"
            p.write_text('["a.c"]\nf = "meh"\n')
            with self.assertRaisesRegex(ValueError, "meh"):
                coverage.load_reasons(p)


class LoadTest(unittest.TestCase):
    def _tree(self, d, audit=None, status="pass"):
        h = pathlib.Path(d) / "Objects/x.c/f"
        h.mkdir(parents=True)
        _write_json(h, "result.json", {"status": status, "tier": "T2",
                                       "covered": {"inv.c": [1, 2]}})
        if audit is not None:
            _write_json(h, "audit.json", audit)
        fx = pathlib.Path(d) / "_fixtures/z"
        fx.mkdir(parents=True)
        _write_json(fx, "result.json", {"status": "pass", "tier": "T1", "covered": {}})

    def test_loads_and_combines_audit(self):
        with tempfile.TemporaryDirectory() as d:
            self._tree(d, audit={"ok": True, "audited_tier": "T1", "problems": []})
            [r] = coverage.load_records(pathlib.Path(d))
            self.assertEqual((r.harness, r.status, r.declared_tier, r.audited_tier,
                              r.audited, r.audit_ok),
                             ("Objects/x.c/f", "pass", "T2", "T1", True, True))
            self.assertEqual(r.covered, {"inv.c": [1, 2]})

    def test_missing_audit(self):
        with tempfile.TemporaryDirectory() as d:
            self._tree(d)
            [r] = coverage.load_records(pathlib.Path(d))
            self.assertFalse(r.audited)
            self.assertEqual(coverage.merge(INV10, [r]).t1, 0)

    def test_audit_not_ok(self):
        with tempfile.TemporaryDirectory() as d:
            self._tree(d, audit={"ok": False, "audited_tier": "T2", "problems": ["x"]})
            [r] = coverage.load_records(pathlib.Path(d))
            self.assertEqual(coverage.merge(INV10, [r]).t1, 0)


class MainTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.d = pathlib.Path(self.tmp.name)

    def _baseline(self, t1):
        return str(_write_json(self.d, "baseline.json", {"t1": t1, "t2": 0, "t3": 0}))

    def _main(self, argv, rep):
        with unittest.mock.patch("sys.stdout"), unittest.mock.patch("sys.stderr"):
            return coverage.main(argv, report=rep)

    def _out(self):
        out = self.d / "out"
        out.mkdir(exist_ok=True)
        _write_json(out, "inventory.json", INV10.to_json())
        h = out / "inv.c/add"
        h.mkdir(parents=True)
        _write_json(h, "result.json", {"status": "pass", "tier": "T1",
                                       "covered": {"inv.c": [1, 2, 3, 4]}})
        return out

    def test_ratchet_fails_on_drop(self):
        # baseline 60 covered lines, report has 59
        rep = dataclasses.replace(coverage.merge(INV10, []), t1=59)
        self.assertEqual(self._main(["--baseline", self._baseline(60)], rep), 1)

    def test_ratchet_passes_at_baseline(self):
        rep = dataclasses.replace(coverage.merge(INV10, []), t1=60)
        self.assertEqual(self._main(["--baseline", self._baseline(60)], rep), 0)

    def test_fail_under(self):
        rep = coverage.merge(INV10, [A_T1])   # 40%
        self.assertEqual(self._main(["--fail-under-t1", "50"], rep), 1)
        self.assertEqual(self._main(["--fail-under-t1", "40"], rep), 0)

    def test_include_unaudited_rejects_gates(self):
        for extra in (["--fail-under-t1", "1"], ["--baseline", self._baseline(0)]):
            with self.assertRaises(SystemExit) as cm, \
                    unittest.mock.patch("sys.stderr"):
                coverage.main(["--include-unaudited", *extra])
            self.assertEqual(cm.exception.code, 2)

    def test_missing_inventory_is_usage_error(self):
        with unittest.mock.patch("sys.stderr"):
            self.assertEqual(coverage.main(["--out", str(self.d / "nowhere")]), 2)

    def test_writes_outputs(self):
        out = self._out()
        with unittest.mock.patch("sys.stdout"):
            rc = coverage.main(["--out", str(out), "--include-unaudited"])
        self.assertEqual(rc, 0)
        data = json.loads((out / "proof-coverage.json").read_text())
        self.assertEqual((data["total_lines"], data["t1"]), (10, 4))
        self.assertTrue(data["provisional"])
        for t in ("t1", "t2", "t3"):
            self.assertTrue((out / f"proof-coverage-{t}.info").exists())
        self.assertIn("inv.c:clamp — no-harness", (out / "unreached.md").read_text())

    def test_unaudited_counts_zero_by_default(self):
        out = self._out()
        with unittest.mock.patch("sys.stdout"):
            self.assertEqual(coverage.main(["--out", str(out)]), 0)
        self.assertEqual(json.loads((out / "proof-coverage.json").read_text())["t1"], 0)

    def test_file_flag(self):
        out = self._out()
        with unittest.mock.patch("sys.stdout") as so:
            rc = coverage.main(["--out", str(out), "--include-unaudited", "--file", "inv.c"])
        self.assertEqual(rc, 0)
        printed = "".join(c.args[0] for c in so.write.call_args_list)
        self.assertIn("clamp", printed)

    def test_file_flag_unknown_file(self):
        out = self._out()
        with unittest.mock.patch("sys.stdout"), unittest.mock.patch("sys.stderr"):
            self.assertEqual(coverage.main(["--out", str(out), "--file", "nope.c"]), 2)


class RealArtifactsSmokeTest(unittest.TestCase):
    def test_real_tree(self):
        out = PROOFS / "out"
        if not (out / "inventory.json").exists():
            self.skipTest("no out/inventory.json")
        rep = coverage.build_report(out, include_unaudited=True)
        self.assertGreater(rep.total_lines, 1000)
        self.assertLessEqual(rep.t3, rep.t2)
        self.assertLessEqual(rep.t2, rep.t1)
        self.assertLessEqual(rep.t1, rep.total_lines)


if __name__ == "__main__":
    unittest.main()
