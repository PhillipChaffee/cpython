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
                         ["harness.c:12: assertion is unreachable (vacuous): dead code: n <= 4"])

    def test_macro_wrapped_unreachable_assertion_is_rejected(self):
        v = audit.audit(FIX / "macro_assert_harness")
        self.assertFalse(v.ok)
        self.assertEqual([p for p in v.problems if "unreachable" in p],
                         ["harness.c:15: assertion is unreachable (vacuous): s == 12345"])

    def test_unreachable_assertion_in_harness_header_is_rejected(self):
        v = audit.audit(FIX / "header_assert_harness")
        self.assertFalse(v.ok)
        self.assertEqual([p for p in v.problems if "unreachable" in p],
                         ["check.h:5: assertion is unreachable (vacuous): dead code in check.h"])

    def test_line_directive_in_harness_code_is_rejected(self):
        v = audit.audit(FIX / "line_directive_harness")
        self.assertFalse(v.ok)
        self.assertIn("harness.c:11: #line directives are not allowed in harness code", v.problems)

    def test_stub_in_harness_header_is_an_unlisted_stub(self):
        v = audit.audit(FIX / "header_stub_harness")
        self.assertFalse(v.ok)
        self.assertIn("provider", " ".join(v.problems))
        listed = lambda t: t + 'harness_stubs = ["provider"]\nharness_stubs_reason = "test"\n'
        v = self.audit_copy(FIX / "header_stub_harness", edit_toml=listed)
        self.assertTrue(v.ok, v.problems)
        self.assertEqual(v.stubs, ["harness:provider"])

    def test_include_of_other_proof_code_is_rejected(self):
        v = audit.audit(FIX / "foreign_include_harness")
        self.assertFalse(v.ok)
        self.assertIn("includes Tools/proofs/tests/fixtures/shared_helper/helper.h, proof code "
                      "outside the harness directory that no audit rule checks", v.problems)

    def test_symlink_out_of_harness_directory_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = _copy(FIX / "loop_harness_ok", tmp,
                      edit_c=lambda c: '#include "check.h"\n' + c)
            outside = pathlib.Path(tmp) / "elsewhere.h"
            outside.write_text("/* outside the harness directory */\n")
            os.symlink(outside, h / "check.h")
            v = audit.audit(h, out=pathlib.Path(tmp) / "out")
        self.assertFalse(v.ok)
        self.assertIn("check.h is a symlink to", " ".join(v.problems))

    def test_header_assumes_counted_by_runner_and_audit(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = _copy(FIX / "loop_harness_ok", tmp,
                      edit_c=lambda c: '#include "bound.h"\n' + c.replace(
                          "int s = sum(a, n);", "bound(n);\n    int s = sum(a, n);"))
            (h / "bound.h").write_text("static void bound(int n) { __CPROVER_assume(n >= 0); }\n")
            out = pathlib.Path(tmp) / "out"
            r = run.run_harness(h, out=out)
            self.assertEqual(r.status, "pass", r.detail)
            self.assertEqual(r.assumes, 3)
            v = audit.audit(h, out=out)
            self.assertEqual(len(v.assumes), r.assumes)
            self.assertIn("bound.h:1: __CPROVER_assume(n >= 0)", v.assumes)
            (h / "bound.h").write_text("static void bound(int n) { assert(n >= 0); }\n")
            r = run.run_harness(h, out=out)
        self.assertEqual(r.status, "error")
        self.assertIn("bound.h calls assert()", r.detail)

    def test_macro_only_model_header_must_be_listed(self):
        # sneaky.h declares no symbol, so the symbol table never shows it.
        with unittest.mock.patch.object(run, "MODELS", FIX / "fixture_models"):
            v = audit.audit(FIX / "macro_model_harness")
        self.assertFalse(v.ok)
        self.assertIn("unlisted model models/sneaky.h is compiled in; "
                      "list it in proof.toml models", v.problems)

    def test_listed_model_header_assumes_are_collected(self):
        with unittest.mock.patch.object(run, "MODELS", FIX / "fixture_models"), \
                tempfile.TemporaryDirectory() as tmp:
            h = _copy(FIX / "unlisted_model_harness", tmp,
                      edit_toml=lambda t: t.replace("models = []", 'models = ["fake_model"]'),
                      edit_c=lambda c: c.replace('#include "fake_model.c"', '#include "fake_model.h"')
                      .replace("    int s =", "    FAKE_BOUND(n);\n    int s ="))
            out = pathlib.Path(tmp) / "out"
            r = run.run_harness(h, out=out)
            v = audit.audit(h, out=out)
        self.assertTrue(v.ok, v.problems)
        self.assertEqual(r.assumes, len(v.assumes))
        self.assertIn("models/fake_model.h:2: __CPROVER_assume((n) <= 4) "
                      "[no ASSUME instruction on this line]", v.assumes)
        self.assertTrue([a for a in v.assumes if a.startswith("harness.c:10: FAKE_BOUND(n); [ASSUME ")],
                        v.assumes)

    def test_file_outside_repository_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = pathlib.Path(tmp) / "helper.h"
            outside.write_text("#define HELPER 1\n")
            h = _copy(FIX / "loop_harness_ok", tmp,
                      edit_c=lambda c: f'#include "{outside}"\n' + c)
            v = audit.audit(h, out=pathlib.Path(tmp) / "out")
        self.assertFalse(v.ok)
        self.assertIn(f"includes {os.path.realpath(outside)}, outside the repository and the "
                      "compiler's system include directories", " ".join(v.problems))

    def test_system_headers_are_allowed(self):
        v = self.audit_copy(FIX / "loop_harness_ok",
                            edit_toml=lambda t: t.replace('"T2"', '"T1"'),
                            edit_c=lambda c: "#include <stddef.h>\n#include <limits.h>\n" + c)
        self.assertTrue(v.ok, v.problems)
        dirs = run.system_include_dirs()
        self.assertTrue(dirs)
        self.assertTrue(any(os.path.exists(os.path.join(d, "limits.h")) for d in dirs), dirs)

    def test_symlink_reached_through_another_path_is_rejected(self):
        # check.h is named through a symlinked directory and points at a
        # repository file: only real paths show that it sits in the harness directory.
        with tempfile.TemporaryDirectory() as tmp:
            link = pathlib.Path(tmp) / "link"
            h = _copy(FIX / "loop_harness_ok", tmp,
                      edit_c=lambda c: f'#include "{link}/loop_harness_ok/check.h"\n' + c)
            os.symlink(tmp, link)
            os.symlink(ROOT / "Include/patchlevel.h", h / "check.h")
            v = audit.audit(h, out=pathlib.Path(tmp) / "out")
        self.assertFalse(v.ok)
        self.assertIn("check.h is a symlink to", " ".join(v.problems))

    def test_macro_wrapped_assume_is_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            h = _copy(FIX / "loop_harness_ok", tmp,
                      edit_toml=lambda t: t.replace('"T2"', '"T1"'),
                      edit_c=lambda c: c.replace(
                          '#include "loop.c"\n', '#include "loop.c"\n#define BOUND(c) __CPROVER_assume(c)\n')
                      .replace("    __CPROVER_assume(0 <= n && n <= 4);", "    BOUND(0 <= n && n <= 4);"))
            out = pathlib.Path(tmp) / "out"
            r = run.run_harness(h, out=out)
            v = audit.audit(h, out=out)
        self.assertTrue(v.ok, v.problems)
        self.assertEqual(r.assumes, len(v.assumes))
        self.assertEqual(len(v.assumes), 3, v.assumes)
        self.assertEqual(v.assumes[0], "harness.c:2: __CPROVER_assume(c) "
                                       "[no ASSUME instruction on this line]")
        self.assertRegex(v.assumes[1], r"^harness\.c:8: BOUND\(0 <= n && n <= 4\); \[ASSUME .*\]$")
        self.assertEqual(v.assumes[2], "harness.c:10: __CPROVER_assume(-1000 <= a[i] && a[i] <= 1000)")

    def test_assume_implied_by_an_assertion_is_not_listed(self):
        loc = "        // {} file harness.c line {} function harness\n"
        listing = (loc.format(1, 3) + "        ASSERT false // no candidate\n"
                   + loc.format(2, 3) + "        ASSUME false\n"           # implied: left out
                   + loc.format(3, 4) + "        ASSERT x > 0 // weaker\n"
                   + loc.format(4, 4) + "        ASSUME x > 1\n"           # another condition
                   + loc.format(5, 5) + "        ASSERT false // skipped by a jump\n"
                   + loc.format(6, 5) + "     1: ASSUME false\n")          # a jump target
        with tempfile.TemporaryDirectory() as tmp:
            src = pathlib.Path(tmp) / "harness.c"
            src.write_text("\n\n__CPROVER_assert(0, \"x\"); __CPROVER_assume(0);\nb();\nc();\n")
            got, stray = run.assumes(listing, tmp, tmp, [os.path.realpath(src)], {})
        self.assertEqual(got, ["harness.c:4: b(); [ASSUME x > 1]", "harness.c:5: c(); [ASSUME false]"])
        self.assertEqual(stray, [])

    def test_assume_injected_into_source_is_rejected(self):
        # A harness macro that expands inside the source puts the assume there.
        inject = ("#define return __CPROVER_assume(s == 0); return\n"
                  '#include "loop.c"\n#undef return\n')
        with tempfile.TemporaryDirectory() as tmp:
            h = _copy(FIX / "loop_harness_ok", tmp,
                      edit_toml=lambda t: t.replace('"T2"', '"T1"'),
                      edit_c=lambda c: c.replace('#include "loop.c"\n', inject))
            out = pathlib.Path(tmp) / "out"
            r = run.run_harness(h, out=out)
            v = audit.audit(h, out=out)
        self.assertEqual(r.assumes, len(v.assumes))
        self.assertFalse(v.ok)
        self.assertIn("Tools/proofs/tests/fixtures/loop.c:5: an assume outside harness code "
                      "and the listed models and contracts", " ".join(v.problems))

    def test_abort_and_exit_in_harness_code_are_assumptions(self):
        r = run.run_harness(FIX / "hidden_abort_harness")
        self.assertEqual(r.status, "pass", r.detail)
        v = audit.audit(FIX / "hidden_abort_harness")
        self.assertTrue(v.ok, v.problems)
        self.assertEqual(v.assumes, [
            "harness.c:10: if (m > 4) abort(); [CALL abort: does not return]",
            "harness.c:11: DIE_IF(n < 0 || n > 4); [CALL exit: does not return]",
            "harness.c:13: __CPROVER_assume(-1000 <= a[i] && a[i] <= 1000)"])
        self.assertEqual(r.assumes, 3)

    def test_call_that_can_reach_abort_is_an_assumption(self):
        # require() is neither harness code nor the target, and may not return.
        # The target's own call of it is behaviour under proof, not an assumption.
        r = run.run_harness(FIX / "guard_harness")
        v = audit.audit(FIX / "guard_harness")
        self.assertTrue(v.ok, v.problems)
        self.assertEqual(v.assumes, ["harness.c:7: require(v >= 0); "
                                     "[CALL require: can reach abort]"])
        self.assertEqual(r.assumes, 1)

    def test_self_loop_fails_the_proof(self):
        # Without --no-self-loops-to-assumptions cbmc turns "for (;;);" into an
        # assumption that no listing shows.
        self.assertIn("--no-self-loops-to-assumptions", run.CHECKS)
        self.assertNotIn("--no-self-loops-to-assumptions", run.DISABLEABLE)
        r = run.run_harness(FIX / "self_loop_harness")
        self.assertEqual(r.status, "fail", r.detail)
        self.assertIn("unwinding assertion", r.detail)
        v = audit.audit(FIX / "self_loop_harness")
        self.assertFalse(v.ok)

    def test_compiler_environment_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            shadow = pathlib.Path(tmp) / "shadow"
            shadow.mkdir()
            (shadow / "limits.h").write_text("#define SHADOWED 1\n")
            h = _copy(FIX / "loop_harness_ok", tmp,
                      edit_toml=lambda t: t.replace('"T2"', '"T1"'),
                      edit_c=lambda c: c.replace('"loop.c"\n', '"loop.c"\n#include <limits.h>\n'))
            plain = run.input_hash(h)
            env = {k: str(shadow) for k in ("CPATH", "C_INCLUDE_PATH", "GCC_EXEC_PREFIX",
                                            "COMPILER_PATH", "LIBRARY_PATH")}
            run.system_include_dirs.cache_clear()
            try:
                with unittest.mock.patch.dict(os.environ, env):
                    self.assertNotIn(os.path.realpath(shadow), run.system_include_dirs())
                    cfg = run._read_config(h)
                    cmd, cwd, _ = run.goto_cc_command(h, cfg, run.GOTO_PLACEHOLDER)
                    deps = run._dependencies(cmd, cwd)
                    self.assertNotIn(os.path.realpath(shadow / "limits.h"), deps)
                    self.assertEqual(run.input_hash(h), plain)
                    v = audit.audit(h, out=pathlib.Path(tmp) / "out")
            finally:
                run.system_include_dirs.cache_clear()
        self.assertTrue(v.ok, v.problems)

    def test_define_before_source_include_is_rejected(self):
        def top(text, extra=""):
            return dict(edit_c=lambda c: text + c,
                        edit_toml=lambda t: t.replace('"T2"', '"T1"') + extra)
        v = self.audit_copy(FIX / "loop_harness_ok", **top("#define LIMIT 4\n"))
        self.assertFalse(v.ok)
        self.assertIn('harness.c:1: #define LIMIT before #include "loop.c" (harness.c:2) can change '
                      "the code under proof; move it below that #include or list LIMIT in "
                      "proof.toml predefine", v.problems)
        listed = 'predefine = ["LIMIT"]\npredefine_reason = "test"\n'
        v = self.audit_copy(FIX / "loop_harness_ok", **top("#define LIMIT 4\n", listed))
        self.assertTrue(v.ok, v.problems)
        v = self.audit_copy(FIX / "loop_harness_ok", **top("#define LIMIT 4\n", 'predefine = ["LIMIT"]\n'))
        self.assertFalse(v.ok)
        self.assertIn("predefine_reason", " ".join(v.problems))
        for text, what in (("# undef \\\n NDEBUG\n", "harness.c:1: #undef NDEBUG before"),
                           ("/* c */ %:define if(x) if (1)\n", "harness.c:1: #define if before"),
                           ("#pragma pack(1)\n", "harness.c:1: #pragma before"),
                           ("_Pragma(\"pack(1)\")\n", "harness.c:1: #pragma before")):
            v = self.audit_copy(FIX / "loop_harness_ok", **top(text))
            self.assertFalse(v.ok, text)
            self.assertIn(what, " ".join(v.problems))
        # In a harness header, included before the source.
        with tempfile.TemporaryDirectory() as tmp:
            h = _copy(FIX / "loop_harness_ok", tmp, **top('#include "pre.h"\n'))
            (h / "pre.h").write_text("\n#define s s_renamed\n")
            v = audit.audit(h, out=pathlib.Path(tmp) / "out")
        self.assertFalse(v.ok)
        self.assertIn("pre.h:2: #define s before", " ".join(v.problems))
        # Below the last outside #include a macro is harness business.
        v = self.audit_copy(FIX / "loop_harness_ok", edit_toml=lambda t: t.replace('"T2"', '"T1"'),
                            edit_c=lambda c: c.replace('"loop.c"\n', '"loop.c"\n#define LIMIT 4\n'))
        self.assertTrue(v.ok, v.problems)

    def test_unused_equivalents_reported_below_t2(self):
        entry = '\n[[equivalent_mutants]]\noffset = 0\ndiff = "a ==> b"\nreason = "r"\n'
        v = self.audit_copy(FIX / "loop_harness_ok",
                            edit_toml=lambda t: t.replace('"T2"', '"T1"') + entry)
        self.assertTrue(v.ok, v.problems)
        self.assertEqual(v.unused_equivalents, ["offset 0: a ==> b"])

    def test_bool_offset_is_rejected(self):
        cfg = {"equivalent_mutants": [{"offset": True, "diff": "a ==> b", "reason": "r"}]}
        with self.assertRaises(audit.AuditError):
            audit._equivalents(cfg)

    def test_unused_equivalent_is_reported_as_a_note(self):
        stale = ('\n[[equivalent_mutants]]\noffset = 99\ndiff = "if (s < 0) ==> if (s <= 0)"\n'
                 'reason = "stale offset"\n')
        v = self.audit_copy(FIX / "strong_harness", edit_toml=lambda t: t + stale)
        self.assertTrue(v.ok, v.problems)
        self.assertEqual(v.audited_tier, "T2", v.problems)
        self.assertEqual(v.unused_equivalents, ["offset 99: if (s < 0) ==> if (s <= 0)"])
        self.assertEqual(audit.audit(FIX / "strong_harness").unused_equivalents, [])

    def test_harness_defined_callee_is_an_unlisted_stub(self):
        v = audit.audit(FIX / "callback_harness")
        self.assertFalse(v.ok)
        text = " ".join(v.problems)
        self.assertIn("provider", text)
        self.assertNotIn("helper", text)            # called by harness(), not by the target
        listed = lambda t: t + 'harness_stubs = ["provider"]\nharness_stubs_reason = "test"\n'
        v = self.audit_copy(FIX / "callback_harness", edit_toml=listed)
        self.assertTrue(v.ok, v.problems)
        self.assertEqual(v.stubs, ["harness:provider"])
        v = self.audit_copy(FIX / "callback_harness",
                            edit_toml=lambda t: t + 'harness_stubs = ["provider"]\n')
        self.assertFalse(v.ok)
        self.assertIn("harness_stubs_reason", " ".join(v.problems))

    def test_vacuous_mutants_are_not_killed(self):
        # Mutants such as "if (v >= 0)" abort() on every input: the proof then
        # "fails" only as vacuous, which must not count as a kill.
        v = audit.audit(FIX / "abort_harness")
        self.assertTrue(v.ok, v.problems)
        self.assertGreater(v.mutants_vacuous, 0)
        self.assertLessEqual(v.mutants_killed + v.mutants_vacuous, v.mutants_total)
        self.assertEqual(v.audited_tier, "T1")
        self.assertIn("vacuous mutant, not killed", " ".join(v.problems))

    def test_equivalent_mutant_needs_matching_offset(self):
        v = self.audit_copy(FIX / "strong_harness", edit_toml=lambda t: t.replace("offset = 1", "offset = 2"))
        self.assertEqual(v.audited_tier, "T1")
        self.assertIn("surviving mutant (line 4, offset 1): if (s < 0) ==> if (s <= 0)",
                      v.problems)

    def test_equivalent_entry_does_not_excuse_errored_mutant(self):
        real = run.run_harness

        def fake(hdir, out=run.OUT, compile_db=None, include_first=(), timeout=run.TIMEOUT):
            r = real(hdir, out, compile_db, include_first, timeout)
            if include_first:                    # every mutant run "times out"
                r.status, r.detail = "error", f"timeout after {timeout}s"
            return r
        with unittest.mock.patch.object(run, "run_harness", fake):
            v = self.audit_copy(FIX / "strong_harness")
        self.assertEqual(v.audited_tier, "T1")
        self.assertEqual((v.mutants_killed, v.mutants_error), (0, v.mutants_total))
        self.assertIn("timeout, not killed (line 4, offset 1): if (s < 0) ==> if (s <= 0)",
                      " ".join(v.problems))

    def test_classify(self):
        R = run.Result
        self.assertEqual(audit.classify(R("fail", "T2", detail="1 failed: x")), "killed")
        self.assertEqual(audit.classify(R("fail", "T2", detail="vacuous: the end ...")), "vacuous")
        self.assertEqual(audit.classify(R("error", "T2", detail="timeout after 3s")), "error")
        self.assertEqual(audit.classify(R("pass", "T2")), "pass")

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
    def test_included_file_flags_and_version_change_input_hash(self):
        # The copy must live inside the repository for its header to be hashed;
        # out/ is ignored by git.
        run.OUT.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=run.OUT) as tmp:
            h = _copy(FIX / "loop_harness_ok", tmp,
                      edit_c=lambda c: '#include "extra.h"\n' + c)
            (h / "extra.h").write_text("#define EXTRA 1\n")
            before = run.input_hash(h)
            (h / "extra.h").write_text("#define EXTRA 2\n")
            self.assertNotEqual(run.input_hash(h), before)
            base = run.input_hash(h)
            src = str((ROOT / "Tools/proofs/tests/fixtures/loop.c").resolve())
            db = {src: {"directory": str(ROOT), "file": src,
                        "arguments": ["cc", "-DFOO=1", "-c", src]}}
            self.assertNotEqual(run.input_hash(h, compile_db=db), base)
            with unittest.mock.patch.object(run.inventory, "cbmc_version", lambda: "cbmc 0.0"):
                self.assertNotEqual(run.input_hash(h), base)
            toml = (h / "proof.toml").read_text().replace("unwind = 5", "unwind = 6")
            (h / "proof.toml").write_text(toml)
            self.assertNotEqual(run.input_hash(h), base)

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
