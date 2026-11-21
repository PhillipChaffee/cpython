# CPython C Proof Coverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a proof harness system that runs machine-checked proofs directly on CPython's unmodified C sources, and a coverage tool that reports what fraction of the C code those proofs cover, then drive that number as high as it will go.

**Architecture:** One CBMC proof harness per C function, living under `Tools/proofs/harnesses/`. Each harness compiles the real source file with the real build flags, so a passing proof is a statement about the C that ships. Callees are replaced by audited contracts or models so every function is proved in isolation (compositional), which is the only way this scales past leaf functions. A runner collects CBMC's `--cover location` output from every passing harness and merges it against an inventory of all executable lines to produce the proof coverage number. An audit gate rejects proofs that are vacuous, under-unwound, or unable to detect planted bugs.

**Tech Stack:** CBMC (`goto-cc`, `goto-instrument`, `cbmc`) for bounded proofs and for unbounded proofs via function and loop contracts; `bear` for the compile database; `universalmutator` for the mutation gate; GenMC for lock-free concurrency; TLA+ (TLC) for one design-level protocol; Python 3 stdlib (`unittest`, `tomllib`, `json`) for the tooling; `lcov`/`genhtml` for the HTML report.

**Spec:** None exists. The requirement is the goal statement: "generate proofs ... that cover 100% (or as close as possible, proven with a code coverage tool) of cpython functionality, that can be verified to be correct on the c code". This header and the two sections below are the spec.

## What "100%" means here, and where it stops

Read this before anything else; it sets what the number can honestly claim.

- **The metric is line-based.** Proof coverage = executable C lines reached by at least one passing, audited harness, divided by all executable C lines in scope. "Functionality" has no countable definition, so lines are the proxy and functions are the secondary count.
- **Every covered line carries a tier.** A single percentage hides how strong the proof is, so the report always shows all three:

| Tier | Claim about the covered line | How it is established |
| --- | --- | --- |
| T1 | No undefined behaviour (overflow, out-of-bounds, bad pointer, division by zero) for all inputs up to the stated bound | CBMC with all safety checks on and unwinding assertions |
| T2 | T1, plus the function's result satisfies a written postcondition, and the harness kills planted mutants | T1 harness plus `assert` on outputs plus mutation gate |
| T3 | T2 for inputs of any size | CBMC function and loop contracts (inductive), no unwind bound |

- **Things this plan cannot prove.** There is no formal specification of the Python language, so "CPython implements Python correctly" is not provable; T2 specs are written by us, function by function. Whole-interpreter proofs are infeasible; every proof is per-function with callees replaced by contracts. Behaviour of the OS, libc, the C compiler, and JIT-generated machine code is outside the proof.
- **100% of lines will not be reached.** Lines that stay unproved are listed in a generated `unreached.md` with a reason code each, so the gap is visible instead of silent.
- **Targets are re-baselined after the pilot.** Task 6 measures real cost per function; the wave targets in Task 10 are starting goals, not commitments.

## Verified by spike (2026-11-21)

The core mechanism was run once, by hand, on this tree before the plan was finalised. No tooling from the tasks below exists yet; these are the facts the tasks build on.

| Check | Result |
| --- | --- |
| CBMC available | `apt-get install cbmc` on Ubuntu 24.04 gives CBMC 5.95.1 with `goto-cc` and `goto-instrument` |
| Real source compiles for proof | A harness that `#include`s `Objects/longobject.c` compiles with `goto-cc` in 3 s. Flags needed: `-DPy_BUILD_CORE -IInclude -IInclude/internal -IInclude/internal/mimalloc -I<build dir> -I. -IObjects`; only `./configure` is needed first, not a full `make` |
| Proof passes on unmodified C | `v_iadd` with `0 <= n <= m <= 4`, digits `<= PyLong_MASK`, `--unwind 6`, all safety checks: `VERIFICATION SUCCESSFUL`, 33 s |
| Proof fails on a planted bug | Replacing `x[i] = carry & PyLong_MASK` with `x[i] = carry` in a copy: `assertion x[i] <= PyLong_MASK: FAILURE` |
| Coverage output is usable | `--cover location --json-ui` reports 6 of 6 blocks of `v_iadd` covered, with exact source lines in a `basicBlockLines` field per goal |
| Denominator comes for free | The same run lists every block of the translation unit: 4,360 blocks in 307 functions for `longobject.c`, 14 covered by this one harness |

What this changes in the tasks:

- **Task 1:** use the apt package (5.95.1) as the pin unless Task 7's spike needs a newer release for contracts.
- **Task 2:** build the inventory from the full goal list of a `--cover location` run per translation unit, using `basicBlockLines`, rather than from `--show-goto-functions`.
- **Task 3:** CBMC checks properties across the whole translation unit (7,420 here), and unreachable ones pass trivially. The runner must report only properties in functions the harness reaches. Roughly 30 s per harness on a large file means the full campaign needs parallel runs; `--drop-unused-functions` did not reduce the time in this spike.
- **Not yet tested:** function and loop contracts (Task 7), models for heap objects (Task 6), `bear`, `universalmutator`, GenMC, TLC.

## Scope coverage of this plan (tool-checked)

`docs/superpowers/plans/2026-11-21-cpython-proof-coverage-scope.py` assigns every tracked `.c` and `.h` file to a Task 10 wave or a named exclusion and exits non-zero if any file is left over. Its rule list is the authoritative file-to-wave mapping; where it and the wave table in Task 10 differ, the script wins. Result on this tree: all 1,156 files assigned, none unassigned.

| Bucket | Files | Lines |
| --- | --- | --- |
| Wave 1: pure leaves | 30 | 11,661 |
| Wave 2: value objects | 9 | 21,873 |
| Wave 3: containers and text | 7 | 43,579 |
| Wave 4: front end | 29 | 49,735 |
| Wave 5: compiler | 11 | 35,931 |
| Wave 6: interpreter core | 20 | 83,872 |
| Wave 7: object model and memory | 13 | 37,937 |
| Wave 8: runtime, remaining `Python/`, `Objects/`, `Programs/` | 139 | 112,007 |
| Wave 9: extension modules | 301 | 447,241 |
| Headers in `Include/`, proved through the harnesses that include them | 289 | 58,926 |
| **In proof scope** | **848** | **902,762 (85.0%)** |
| Excluded: `Modules/_hacl/`, verified upstream in F* | 47 | 22,319 |
| Excluded: vendored third-party | 63 | 39,018 |
| Excluded: other platforms | 50 | 17,632 |
| Excluded: JIT machine-code emission | 10 | 3,167 |
| Excluded: test, demo and example code | 137 | 76,544 |
| Excluded: build configuration probes | 1 | 316 |

These are raw file lines, not executable lines, and this shows what the plan targets, not what has been proved. The excluded 15% is not CPython functionality on the in-scope platform, apart from the JIT emitter and the vendored libraries, which are named so the gap is explicit.

## Global Constraints

- Proofs run on unmodified sources. No edits to any file outside `Tools/proofs/`, `docs/superpowers/`, and `.github/workflows/proofs.yml`.
- One build configuration is in scope: Linux x86-64, `./configure` with no options, built out-of-tree in `build/proofs/`. The free-threaded build (`--disable-gil`) is a second configuration used only by Task 8.
- Denominator exclusions, and nothing else: `Modules/_hacl/` (already verified in F*), `Modules/expat/`, `Modules/_decimal/libmpdec/`, `Objects/mimalloc/` and `Include/internal/mimalloc/`, `Modules/_ctypes/` vendored libffi shims, `PC/`, `Python/jit*.c`, `Python/perf_jit_trampoline.c`, `Modules/_test*`, `Modules/_xx*`, `Modules/xx*`.
- Generated C (`Parser/parser.c`, `Python/generated_cases.c.h`, `Python/Python-ast.c`, clinic output) stays in the denominator; it is code that runs.
- Every CBMC proof run uses exactly these checks: `--bounds-check --pointer-check --pointer-overflow-check --signed-overflow-check --unsigned-overflow-check --conversion-check --div-by-zero-check --undefined-shift-check --float-overflow-check --nan-check --pointer-primitive-check --unwinding-assertions`. Where CPython relies on defined unsigned wraparound, `--unsigned-overflow-check` and `--conversion-check` may be disabled per harness in `proof.toml` with a written reason.
- Tool versions are pinned in `Tools/proofs/versions.env` in Task 1 and never floated.
- Tooling is Python stdlib only, tested with `unittest`, run by the system `python3` (not the CPython under proof).
- A harness counts toward coverage only if it passes `audit.py`.

## Review Focus

The five ways this system reports a number that is not true, most likely first. Each has a pinning test in the named task.

1. **Vacuous proof:** `__CPROVER_assume` constraints contradict each other, so CBMC proves everything about nothing. Expected: audit fails the harness. Test in Task 5 (`test_contradictory_assume_is_rejected`).
2. **Too-small unwind bound:** a loop is cut off early and the lines after it look proved. Expected: `--unwinding-assertions` fails the run; a harness without it is rejected. Test in Task 3 (`test_insufficient_unwind_fails`).
3. **Stub hides behaviour:** a callee model returns only the happy value, so error paths in the caller look covered but were never checked against real callee behaviour. Expected: every model must have a nondeterministic failure return; audit lists every stub per harness. Test in Task 6 (`test_model_alloc_can_fail`).
4. **Reach without check:** lines are reached by a harness that asserts nothing, and are reported as if functionally proved. Expected: such lines are T1 only, never T2. Test in Task 4 (`test_tier_without_assertions_is_t1`) and Task 5 (`test_surviving_mutant_blocks_t2`).
5. **Build drift:** proofs were run under different macros than the real build (`#ifdef` branches differ), so the covered lines are not the shipped lines. Expected: runner refuses to run if `compile_commands.json` is older than `pyconfig.h` or `Makefile`. Test in Task 3 (`test_stale_compile_db_is_refused`).

---

## File Structure

| Path | Responsibility |
| --- | --- |
| `Tools/proofs/install.sh` | Install pinned CBMC, bear, universalmutator, lcov |
| `Tools/proofs/versions.env` | Pinned tool versions |
| `Tools/proofs/build_db.sh` | Configure and build in `build/proofs/`, emit `compile_commands.json` |
| `Tools/proofs/scope.toml` | Denominator exclusions (the list in Global Constraints) |
| `Tools/proofs/inventory.py` | Denominator: every executable line and function in scope |
| `Tools/proofs/run.py` | Run one or all harnesses; write per-harness results |
| `Tools/proofs/coverage.py` | Merge results into the coverage report; enforce thresholds |
| `Tools/proofs/audit.py` | Vacuity, unwind, stub census, mutation gate |
| `Tools/proofs/new_harness.py` | Scaffold a harness directory for a function |
| `Tools/proofs/models/` | Shared models: allocator, object constructors, error indicator, generic callee |
| `Tools/proofs/contracts/` | Function contracts used to replace callees, one header per source file |
| `Tools/proofs/harnesses/<src path>/<function>/` | `harness.c` and `proof.toml` for one function |
| `Tools/proofs/concurrency/` | GenMC harnesses and the TLA+ spec |
| `Tools/proofs/tests/` | `unittest` tests and small C fixtures for the tooling |
| `Tools/proofs/baseline.json` | Committed coverage floor for the CI ratchet |
| `Tools/proofs/README.md` | How to run, how to read the report, tier definitions |
| `.github/workflows/proofs.yml` | CI |

Generated, not committed (add to `.gitignore`): `build/proofs/`, `Tools/proofs/out/`.

---

### Task 1: Toolchain and compile database

**Files:**
- Create: `Tools/proofs/install.sh`, `Tools/proofs/versions.env`, `Tools/proofs/build_db.sh`, `Tools/proofs/README.md`
- Create: `Tools/proofs/tests/test_toolchain.py`, `Tools/proofs/tests/fixtures/smoke.c`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `build/proofs/compile_commands.json` (one entry per compiled `.c`, standard `directory`/`file`/`arguments` keys); `cbmc`, `goto-cc`, `goto-instrument`, `bear`, `mutate` on `PATH`; `Tools/proofs/versions.env` with `CBMC_VERSION=`, `BEAR_VERSION=`, `UNIVERSALMUTATOR_VERSION=`.

- [ ] **Step 1: Write the failing test** in `Tools/proofs/tests/test_toolchain.py`

```python
class ToolchainTest(unittest.TestCase):
    def test_cbmc_proves_and_refutes(self):
        # smoke.c: int f(int x){ if (x == INT_MAX) return x; return x + 1; }
        #          int g(int x){ return x + 1; }
        ok = run(["cbmc", FIX / "smoke.c", "--function", "f", "--signed-overflow-check"])
        bad = run(["cbmc", FIX / "smoke.c", "--function", "g", "--signed-overflow-check"])
        self.assertEqual(ok.returncode, 0)
        self.assertEqual(bad.returncode, 10)   # CBMC: 10 = property violated

    def test_compile_db_covers_core_sources(self):
        db = json.load(open(ROOT / "build/proofs/compile_commands.json"))
        files = {os.path.relpath(e["file"], ROOT) for e in db}
        for f in ("Objects/longobject.c", "Python/ceval.c", "Parser/parser.c"):
            self.assertIn(f, files)
```

- [ ] **Step 2: Run it.** `python3 -m unittest Tools.proofs.tests.test_toolchain -v` — expected: ERROR, `cbmc` not found.
- [ ] **Step 3: Write `install.sh`.** Install `cbmc` from apt (5.95.1 on Ubuntu 24.04, confirmed working), `bear` from apt, `universalmutator` from pip, `lcov` from apt. Write the exact versions resolved into `versions.env`, then change the script to install those pinned versions.
- [ ] **Step 4: Write `build_db.sh`.** `mkdir -p build/proofs && cd build/proofs && ../../configure && bear -- make -j"$(nproc)"`.
- [ ] **Step 5: Run both scripts, then the test.** Expected: 2 tests PASS.
- [ ] **Step 6: Add `build/proofs/` and `Tools/proofs/out/` to `.gitignore`; write the README section "Setup"; commit.**

---

### Task 2: Inventory (the denominator)

**Files:**
- Create: `Tools/proofs/scope.toml`, `Tools/proofs/inventory.py`
- Test: `Tools/proofs/tests/test_inventory.py`, `Tools/proofs/tests/fixtures/inv.c`

**Interfaces:**
- Consumes: `build/proofs/compile_commands.json`.
- Produces: `inventory.build(compile_db: Path, scope: Path) -> Inventory`; CLI `python3 Tools/proofs/inventory.py --out Tools/proofs/out/inventory.json`. JSON shape: `{"files": {"<repo-relative path>": {"functions": {"<name>": {"lines": [int, ...], "static": bool}}}}, "excluded": ["<path>", ...]}`. A line is "executable" if it appears in the `basicBlockLines` of any goal in `cbmc --cover location --json-ui` for its translation unit (see "Verified by spike").

- [ ] **Step 1: Write the failing tests**

```python
def test_counts_executable_lines_only(self):
    # inv.c has 2 functions; comments, blank lines and declarations are not executable
    inv = inventory.build_from_sources([FIX / "inv.c"], scope=EMPTY_SCOPE)
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
    self.assertNotIn("dead_fn", inv_fixture.files["inv.c"].functions)
```

- [ ] **Step 2: Run to verify failure.** `python3 -m unittest Tools.proofs.tests.test_inventory -v` — FAIL, module missing.
- [ ] **Step 3: Write `scope.toml`** with key `exclude = [...]` holding the glob list from Global Constraints verbatim.
- [ ] **Step 4: Implement `inventory.py`.** For each compile-database entry in scope, run `goto-cc` with that entry's `-I`/`-D` flags, then `cbmc --cover location --json-ui` with an empty entry function, and collect `basicBlockLines` per function from every goal, satisfied or not. Lines from headers (`static inline`) are attributed to the header path.
- [ ] **Step 5: Run tests — PASS. Run the CLI on the real tree** and record the totals (files, functions, lines) in the README under "Denominator".
- [ ] **Step 6: Commit.**

---

### Task 3: Harness format, runner, and the first three real proofs

**Files:**
- Create: `Tools/proofs/run.py`, `Tools/proofs/new_harness.py`
- Create: `Tools/proofs/harnesses/Objects/bytes_methods.c/_Py_bytes_isalnum/{harness.c,proof.toml}`
- Create: `Tools/proofs/harnesses/Python/mystrtoul.c/PyOS_strtoul/{harness.c,proof.toml}`
- Create: `Tools/proofs/harnesses/Objects/longobject.c/v_iadd/{harness.c,proof.toml}`
- Test: `Tools/proofs/tests/test_run.py`, fixtures `loop.c`, `loop_harness/`

**Interfaces:**
- Consumes: `compile_commands.json`.
- Produces:
  - `proof.toml` schema: `source = "Objects/longobject.c"`, `function = "v_iadd"`, `tier = "T1" | "T2" | "T3"`, `unwind = 9` (omit for T3), `max_size = 8`, `models = ["alloc"]`, `contracts = []`, `disable_checks = []`, `disable_reason = ""`.
  - Harness convention: `harness.c` contains `void harness(void)`; static functions are reached by `#include`-ing the source file from the harness.
  - `run.run_harness(dir: Path) -> Result` with fields `status: "pass" | "fail" | "error"`, `tier: str`, `covered: dict[str, list[int]]` (path to lines), `assumes: int`, `stubs: list[str]`, `seconds: float`; written to `Tools/proofs/out/<harness path>/result.json`.
  - CLI: `python3 Tools/proofs/run.py [--only <glob>] [--jobs N]`; exit 0 only if every harness passes.

- [ ] **Step 1: Write the failing tests**

```python
def test_passing_harness_reports_covered_lines(self):
    r = run.run_harness(FIX / "loop_harness_ok")       # loop.c: sum of n<=4 ints
    self.assertEqual(r.status, "pass")
    self.assertEqual(r.covered["loop.c"], [2, 3, 4, 5])

def test_insufficient_unwind_fails(self):               # Review Focus 2
    r = run.run_harness(FIX / "loop_harness_unwind2")   # same loop, unwind = 2
    self.assertEqual(r.status, "fail")
    self.assertIn("unwinding assertion", r.detail)

def test_stale_compile_db_is_refused(self):             # Review Focus 5
    os.utime(BUILD / "pyconfig.h")                       # make it newer than the db
    with self.assertRaisesRegex(run.StaleBuild, "build_db.sh"):
        run.load_compile_db(BUILD)

def test_real_harnesses_pass(self):
    for h in ("Objects/bytes_methods.c/_Py_bytes_isalnum",
              "Python/mystrtoul.c/PyOS_strtoul",
              "Objects/longobject.c/v_iadd"):
        self.assertEqual(run.run_harness(HARNESSES / h).status, "pass", h)
```

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement `run.py`.** Per harness: `goto-cc` the harness with the source file's flags from the compile database plus `-I Tools/proofs/models -I Tools/proofs/contracts`; run `cbmc --function harness --unwind N` with the Global Constraints check list; if it passes, run again with `--cover location --json-ui` and parse covered source lines. Count `__CPROVER_assume` occurrences in the harness and models into `assumes`.
- [ ] **Step 4: Implement `new_harness.py <source> <function>`**: creates the directory with a `proof.toml` at tier T1 and a `harness.c` skeleton that declares nondeterministic arguments.
- [ ] **Step 5: Write the three real harnesses.**
  - `_Py_bytes_isalnum`: nondet `len` in `[0, 8]`, buffer of `len` bytes. T2 postcondition: result is `Py_True` exactly when `len > 0` and every byte is an ASCII letter or digit.
  - `PyOS_strtoul`: nondet NUL-terminated string up to 12 bytes, nondet `base`. T1 only, with `assume(base == 0 || (base >= 2 && base <= 36))`; also assert `*ptr` points inside the buffer.
  - `v_iadd`: nondet `m`, `n` with `0 <= n <= m <= 8`, digit arrays with every digit `<= PyLong_MASK`. T2 postcondition: every output digit `<= PyLong_MASK` and returned carry is 0 or 1.
- [ ] **Step 6: Run tests — PASS. If a real harness fails, that is a finding:** record the counterexample trace in `Tools/proofs/findings/<function>.md`, do not weaken the harness to make it pass without a written precondition justified from the function's callers.
- [ ] **Step 7: Commit.**

---

### Task 4: Coverage report

**Files:**
- Create: `Tools/proofs/coverage.py`
- Test: `Tools/proofs/tests/test_coverage.py`

**Interfaces:**
- Consumes: `out/inventory.json` (Task 2), `out/**/result.json` (Task 3), audit verdicts `out/**/audit.json` (Task 5; until Task 5 lands, a missing `audit.json` is treated as "not audited" and the harness counts for nothing).
- Produces: `coverage.merge(inventory, results) -> Report`; CLI `python3 Tools/proofs/coverage.py [--fail-under-t1 PCT] [--baseline Tools/proofs/baseline.json]` writing `out/proof-coverage.json`, `out/proof-coverage.info` (lcov format, one file per tier), `out/unreached.md`. JSON shape: `{"total_lines": int, "t1": int, "t2": int, "t3": int, "by_dir": {"Objects": {...}}, "by_file": {...}, "functions_total": int, "functions_t1": int, ...}`. A line's tier is the highest tier among audited passing harnesses that reach it; T2 and T3 lines also count as T1.

- [ ] **Step 1: Write the failing tests**

```python
def test_percentages_are_exact(self):
    # inventory: 10 lines; harness A (T1) covers 1-4, harness B (T2) covers 3-6
    rep = coverage.merge(INV10, [A_T1, B_T2])
    self.assertEqual((rep.t1, rep.t2, rep.t3, rep.total_lines), (6, 4, 0, 10))

def test_tier_without_assertions_is_t1(self):           # Review Focus 4
    rep = coverage.merge(INV10, [dataclasses.replace(B_T2, audited_tier="T1")])
    self.assertEqual(rep.t2, 0)

def test_failed_or_unaudited_harness_counts_zero(self):
    self.assertEqual(coverage.merge(INV10, [A_FAILED, B_UNAUDITED]).t1, 0)

def test_lines_outside_inventory_are_ignored(self):
    self.assertEqual(coverage.merge(INV10, [COVERS_LINE_99]).t1, 0)

def test_ratchet_fails_on_drop(self):
    self.assertEqual(coverage.main(["--baseline", str(BASE_60PCT)], report=REP_59PCT), 1)

def test_unreached_lists_every_uncovered_function_with_reason(self):
    text = coverage.render_unreached(rep, reasons={"clamp": "unwind-infeasible"})
    self.assertIn("inv.c:clamp — unwind-infeasible", text)
    self.assertIn("inv.c:add — no-harness", text)
```

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement `coverage.py`.** Reason codes for `unreached.md` come from an optional `Tools/proofs/unreached_reasons.toml`; allowed values: `no-harness`, `unwind-infeasible`, `platform-ifdef`, `os-behaviour`, `inline-asm`, `solver-timeout`, `needs-contract`.
- [ ] **Step 4: Run tests — PASS. Run on the real tree** after Task 3's harnesses; `genhtml out/proof-coverage.info -o out/html` must render.
- [ ] **Step 5: Document "Reading the report" in the README; commit.**

---

### Task 5: Audit gate

**Files:**
- Create: `Tools/proofs/audit.py`
- Test: `Tools/proofs/tests/test_audit.py`, fixtures `vacuous_harness/`, `weak_harness/`

**Interfaces:**
- Consumes: harness dirs, `run.run_harness`.
- Produces: `audit.audit(dir: Path) -> Verdict` with `ok: bool`, `audited_tier: str`, `problems: list[str]`, `stubs: list[str]`, `assumes: list[str]` (source text of each assume), `mutants_total: int`, `mutants_killed: int`; written to `out/<harness>/audit.json`. CLI `python3 Tools/proofs/audit.py [--only <glob>]`, exit 1 if any `ok` is false.

Rules the audit enforces:
1. `--unwinding-assertions` was on and no check is disabled without `disable_reason`.
2. Not vacuous: `cbmc --cover cover` on a `__CPROVER_cover(1)` placed as the last statement of `harness()` is satisfiable, and every `assert` in the harness is reachable.
3. Every model and contract the harness links is listed in `proof.toml`; unlisted ones fail.
4. For declared T2 or T3: run `mutate` on the target function only (not the whole file), re-run the proof per mutant, and require every non-equivalent mutant to fail the proof. Survivors are listed; a survivor demotes `audited_tier` to T1 unless it is listed in `proof.toml` under `equivalent_mutants` with a reason.
5. For declared T3: `proof.toml` has no `unwind`, and the run used `goto-instrument --dfcc harness --enforce-contract <function> --apply-loop-contracts`.

- [ ] **Step 1: Write the failing tests**

```python
def test_contradictory_assume_is_rejected(self):        # Review Focus 1
    v = audit.audit(FIX / "vacuous_harness")            # assume(n > 4); assume(n < 2);
    self.assertFalse(v.ok)
    self.assertIn("vacuous", " ".join(v.problems))

def test_surviving_mutant_blocks_t2(self):              # Review Focus 4
    v = audit.audit(FIX / "weak_harness")               # declares T2, asserts only `r >= 0`
    self.assertEqual(v.audited_tier, "T1")
    self.assertLess(v.mutants_killed, v.mutants_total)

def test_unlisted_model_is_rejected(self): ...
def test_disabled_check_needs_reason(self): ...
def test_v_iadd_keeps_t2(self):
    self.assertEqual(audit.audit(HARNESSES / "Objects/longobject.c/v_iadd").audited_tier, "T2")
```

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement `audit.py`.**
- [ ] **Step 4: Run tests — PASS.** If `v_iadd` drops to T1, strengthen its postcondition to the full sum relation over the digit arrays until all mutants die.
- [ ] **Step 5: Commit.**

---

### Task 6: Object models, callee contracts, and the pilot measurement

**Files:**
- Create: `Tools/proofs/models/alloc.c`, `models/object.c`, `models/errors.c`, `models/callee.c`, `models/models.h`
- Create: `Tools/proofs/contracts/longobject.h`
- Create harnesses: `Objects/longobject.c/{x_add,x_sub,v_isub,v_lshift,v_rshift,inplace_divrem1}`, `Objects/bytes_methods.c/_Py_bytes_lower`, `Python/pyhash.c/Py_HashBuffer`
- Create: `Tools/proofs/pilot-report.md`
- Test: `Tools/proofs/tests/test_models.py`

**Interfaces:**
- Produces (all in `models.h`):
  - `alloc.c`: replacements for `PyMem_Malloc`, `PyMem_Calloc`, `PyMem_Realloc`, `PyMem_Free`, `PyObject_Malloc`, `PyObject_Free`, `_PyObject_GC_New*`; each allocating function returns `NULL` nondeterministically.
  - `object.c`: `PyLongObject *model_nondet_long(Py_ssize_t max_digits)`, `PyObject *model_nondet_bytes(Py_ssize_t max_len)`, `PyObject *model_nondet_unicode(Py_ssize_t max_len)`, `PyObject *model_nondet_object(void)`; each returns a well-formed object with refcount `>= 1`, or is documented as never returning `NULL`.
  - `errors.c`: a single ghost `PyObject *model_current_exception`; replacements for `PyErr_SetString`, `PyErr_NoMemory`, `PyErr_Occurred`, `PyErr_Clear`; helper `bool model_error_is_set(void)`.
  - `callee.c`: `PyObject *model_generic_call(void)` — the stand-in for any call through a `tp_*` slot or function pointer: returns either `NULL` with the error set, or a `model_nondet_object()`.
  - `contracts/<file>.h`: CBMC `__CPROVER_requires` / `__CPROVER_ensures` / `__CPROVER_assigns` contracts for functions that other harnesses replace with `--replace-call-with-contract`. A contract may be used for replacement only if a harness for that function enforces the same contract.

- [ ] **Step 1: Write the failing tests**

```python
def test_model_alloc_can_fail(self):                    # Review Focus 3
    # harness asserts PyMem_Malloc(8) != NULL; proof must FAIL
    self.assertEqual(run.run_harness(FIX / "alloc_never_null").status, "fail")

def test_model_objects_are_well_formed(self):
    # asserts on model_nondet_long(4): size in [0,4], every digit <= PyLong_MASK, refcnt >= 1
    self.assertEqual(run.run_harness(FIX / "model_long_wf").status, "pass")

def test_generic_call_null_implies_error_set(self): ...
def test_contract_without_enforcing_harness_is_rejected(self):
    self.assertIn("no enforcing harness", " ".join(audit.audit(FIX / "orphan_contract").problems))
def test_pilot_harnesses_pass_audit(self): ...           # the 8 new harnesses
```

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Implement the models**, then add the orphan-contract rule to `audit.py`.
- [ ] **Step 4: Write the 8 pilot harnesses.** `x_add` and `x_sub` are T2: on non-`NULL` return, the result's digits are normalized and in range; on `NULL`, `model_error_is_set()`. Write `contracts/longobject.h` for `v_iadd` and `v_isub`, enforce them in their own harnesses, and replace them by contract in one caller harness to prove the mechanism end to end.
- [ ] **Step 5: Run `run.py`, `audit.py`, `coverage.py`.** All pass.
- [ ] **Step 6: Write `pilot-report.md`** with measured values: harnesses written, median and worst CBMC wall time, lines covered per harness, findings count, and the measured cost per 1,000 lines. Re-baseline the Task 10 wave targets from these numbers and edit them in this plan.
- [ ] **Step 7: Commit.**

---

### Task 7: Unbounded proofs (T3) with contracts

**Files:**
- Modify: harnesses for `v_iadd`, `v_isub`, `v_lshift`, `v_rshift`, `inplace_divrem1`, `_Py_bytes_isalnum`, `_Py_bytes_lower` (add `harness_t3.c` and a second `proof.toml` table `[t3]` alongside the bounded proof)
- Modify: `Tools/proofs/run.py` (T3 mode), `Tools/proofs/README.md`
- Test: `Tools/proofs/tests/test_t3.py`, fixture `sum_loop_contract/`

**Interfaces:**
- Consumes: Task 5 rule 5.
- Produces: `run.py` T3 mode: `goto-instrument --dfcc harness --enforce-contract <function> --apply-loop-contracts`, then `cbmc` with no `--unwind`. Loop invariants cannot be written in the unmodified source, so they are supplied in `harness_t3.c` by including the source with a per-harness macro header `loop_contracts.h` that defines the invariant macros CBMC reads; if a loop cannot be annotated without editing the source, the function stays T2 and gets reason `needs-contract` in `unreached_reasons.toml` for the T3 column only.

- [ ] **Step 1: Write the failing tests**

```python
def test_loop_contract_proves_unbounded(self):
    r = run.run_harness(FIX / "sum_loop_contract")       # no unwind key, any n
    self.assertEqual((r.status, r.tier), ("pass", "T3"))
def test_wrong_invariant_fails(self): ...
def test_v_iadd_is_t3(self):
    self.assertEqual(audit.audit(HARNESSES / "Objects/longobject.c/v_iadd").audited_tier, "T3")
```

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Spike the invariant-injection mechanism on the fixture first.** If CBMC cannot attach loop contracts without source edits, stop and record that in the README: T3 is then limited to loop-free functions and functions proved through Frama-C WP in a later plan. Do not edit CPython sources to work around it.
- [ ] **Step 4: Add T3 mode to `run.py`; write the T3 harnesses.**
- [ ] **Step 5: Run tests — PASS; commit.**

---

### Task 8: Concurrency track (free-threaded build)

**Files:**
- Create: `Tools/proofs/concurrency/install_genmc.sh`, `concurrency/run_genmc.py`
- Create: `Tools/proofs/concurrency/genmc/{pymutex.c,once_flag.c,rwmutex.c}`
- Create: `Tools/proofs/concurrency/tla/QSBR.tla`, `QSBR.cfg`
- Modify: `Tools/proofs/coverage.py` (new `concurrency` column)
- Test: `Tools/proofs/tests/test_concurrency.py`, fixture `racy_counter.c`

**Interfaces:**
- Produces: `run_genmc.run(client: Path) -> Result` (same `Result` type as Task 3; `tier` is `"C"`); results under `out/concurrency/`. `coverage.py` reports, per function in `Python/lock.c`, `Python/parking_lot.c`, `Python/qsbr.c`, whether a passing GenMC client or TLC model covers it. This column is separate and never added into the T1/T2/T3 line percentages.

Sequential CBMC proofs say nothing about thread interleavings, so lock-free code needs its own tool. Scope is deliberately small: three primitives and one protocol.

- [ ] **Step 1: Write the failing tests**

```python
def test_genmc_finds_planted_race(self):
    self.assertEqual(run_genmc.run(FIX / "racy_counter.c").status, "fail")
def test_pymutex_mutual_exclusion(self):
    # 2 threads, each: PyMutex_Lock; counter++; PyMutex_Unlock  => assert counter == 2
    self.assertEqual(run_genmc.run(GENMC / "pymutex.c").status, "pass")
def test_once_flag_runs_once(self): ...
def test_qsbr_model_checks(self):
    # TLC on QSBR.tla with 3 threads: invariant NoUseAfterFree, property EventuallyFreed
    self.assertEqual(run_tlc(TLA / "QSBR.tla").status, "pass")
```

- [ ] **Step 2: Run to verify failure.**
- [ ] **Step 3: Install GenMC and TLA+ tools** (pin versions into `versions.env`). GenMC clients compile the real `Python/lock.c` from a `--disable-gil` build in `build/proofs-ft/`; the parking lot's OS futex wait is replaced by a spin model, listed as a stub.
- [ ] **Step 4: Write the clients and the TLA+ spec.** The TLA+ spec models `Python/qsbr.c`'s global and per-thread sequence counters. It is a model, not the C: mark it `model-only` in the report and add a trace-validation follow-up to the README's "Known gaps".
- [ ] **Step 5: Run tests — PASS; commit.**

---

### Task 9: CI and the ratchet

**Files:**
- Create: `.github/workflows/proofs.yml`, `Tools/proofs/baseline.json`
- Modify: `Tools/proofs/run.py` (`--changed-since <ref>`), `Tools/proofs/README.md`
- Test: `Tools/proofs/tests/test_changed.py`

**Interfaces:**
- Produces: `run.py --changed-since origin/main` selects harnesses whose `source`, harness directory, linked models, or linked contracts changed. `baseline.json`: `{"t1": int, "t2": int, "t3": int}` (covered line counts).

- [ ] **Step 1: Write the failing test**

```python
def test_changed_source_selects_its_harnesses(self):
    sel = run.select_changed(changed=["Objects/longobject.c"])
    self.assertIn("Objects/longobject.c/v_iadd", sel)
    self.assertNotIn("Python/mystrtoul.c/PyOS_strtoul", sel)
def test_changed_model_selects_all_users(self): ...
```

- [ ] **Step 2: Run to verify failure; implement; run — PASS.**
- [ ] **Step 3: Write `proofs.yml`.** Pull requests: `install.sh`, `build_db.sh`, tooling unit tests, `run.py --changed-since`, `audit.py` on the same set. Nightly: full `run.py`, `audit.py`, `coverage.py --baseline Tools/proofs/baseline.json`, upload `out/html` and `out/unreached.md` as artifacts. The nightly fails if any tier's covered-line count is below baseline.
- [ ] **Step 4: Generate `baseline.json` from the current state; commit.** Each later wave raises the baseline in the same commit that adds its harnesses.

---

### Task 10: The coverage campaign

This task repeats one procedure per source file until the report stops moving. It is the bulk of the work; Tasks 1–9 exist to make it measurable and safe to hand to parallel workers.

**Files:**
- Create: `Tools/proofs/harnesses/**`, `Tools/proofs/contracts/*.h`, `Tools/proofs/unreached_reasons.toml`, `Tools/proofs/findings/*.md`
- Create: `Tools/proofs/gen_bytecode_harnesses.py`, `Tools/proofs/gen_parser_harnesses.py` (waves 4 and 6)
- Modify: `Tools/proofs/baseline.json`

**Interfaces:**
- Consumes: everything above.
- Produces: harnesses and contracts in the Task 3 and Task 6 formats; nothing new.

**Per-file procedure** (one worker owns one source file at a time):

- [ ] **Step 1:** `python3 Tools/proofs/coverage.py --file <path>` to list the file's unproved functions, leaves first (functions whose callees are all proved, modelled, or contracted).
- [ ] **Step 2:** For each function: `new_harness.py`, write the harness at T1, derive preconditions only from what every caller guarantees (cite the caller in a comment), run, and fix until it passes or produces a real counterexample.
- [ ] **Step 3:** A real counterexample goes to `findings/<function>.md` with the trace and a reproducer; the function stays unproved until the finding is triaged. Never add an assume to hide it.
- [ ] **Step 4:** Raise to T2 where the function has a statable result (returns a value, fills a buffer, or sets the error indicator); pass the mutation gate.
- [ ] **Step 5:** If other functions call it, write its contract in `contracts/<file>.h` and enforce it in its own harness.
- [ ] **Step 6:** For anything left, add a reason code to `unreached_reasons.toml`.
- [ ] **Step 7:** Run `run.py --only '<path>/*'`, `audit.py --only '<path>/*'`, `coverage.py`; raise `baseline.json`; commit one file per commit.

**Waves, in order.** Targets are T1 line coverage of the wave's files and are starting goals to be replaced by Task 6's measured numbers.

| Wave | Files | Starting T1 target | Notes |
| --- | --- | --- | --- |
| 1. Pure leaves | `Python/pyctype.c`, `mystrtoul.c`, `pystrhex.c`, `pyhash.c`, `pystrcmp.c`, `pymath.c`, `dtoa.c`, `pystrtod.c`, `Objects/bytes_methods.c`, `Objects/stringlib/*` | 95% | Mostly T2; `dtoa.c` will need large bounds and may stall on solver time |
| 2. Value objects | `Objects/longobject.c`, `floatobject.c`, `complexobject.c`, `boolobject.c`, `bytesobject.c`, `bytearrayobject.c`, `tupleobject.c`, `rangeobject.c`, `sliceobject.c` | 85% | First heavy use of contracts |
| 3. Containers and text | `Objects/listobject.c`, `dictobject.c`, `setobject.c`, `unicodeobject.c`, `unicodectype.c`, `memoryobject.c` | 75% | `dictobject.c` and `unicodeobject.c` are the largest hand-written files |
| 4. Front end | `Parser/lexer/*`, `Parser/tokenizer/*`, `Parser/pegen.c`, `Parser/action_helpers.c`, `Parser/string_parser.c`, `Parser/parser.c` | 70% | `parser.c` is 26k generated lines of one shape: write `gen_parser_harnesses.py` to emit one harness per rule function with sibling rules replaced by a single generic rule contract |
| 5. Compiler | `Python/symtable.c`, `compile.c`, `codegen.c`, `flowgraph.c`, `assemble.c`, `ast*.c`, `Python-ast.c` | 65% | T2 for `flowgraph.c` passes: output CFG well-formedness |
| 6. Interpreter core | `Python/bytecodes.c` via `generated_cases.c.h`, `ceval.c`, `frame.c`, `Objects/frameobject.c`, `codeobject.c`, `funcobject.c`, `genobject.c`, `call.c`, `Python/specialize.c`, `optimizer*.c` | 60% | Write `gen_bytecode_harnesses.py` on top of `Tools/cases_generator` to emit one harness per instruction: nondet well-formed frame and stack of the instruction's declared depth, every call out replaced by `model_generic_call`. T1 plus a stack-effect postcondition |
| 7. Object model and memory | `Objects/object.c`, `typeobject.c`, `abstract.c`, `descrobject.c`, `classobject.c`, `exceptions.c`, `weakrefobject.c`, `obmalloc.c`, `Python/gc.c` | 55% | `typeobject.c` is dominated by slot dispatch; expect many `needs-contract` entries |
| 8. Runtime | `Python/pystate.c`, `pylifecycle.c`, `initconfig.c`, `import.c`, `sysmodule.c`, `bltinmodule.c`, `errors.c`, `ceval_gil.c`, `thread*.c`, remaining `Python/` and `Objects/` | 45% | Much OS interaction: reason `os-behaviour` is legitimate here |
| 9. Extension modules | `Modules/` in scope, one directory at a time, parsers and codecs first (`binascii.c`, `_struct.c`, `_json.c`, `_pickle.c`, `_csv.c`, `zlibmodule.c`, `cjkcodecs/`, `_sre/`) | 40% | Largest area by lines; OS wrappers (`posixmodule.c`, `socketmodule.c`) are mostly argument-marshalling proofs with the syscall stubbed |

**Stop condition:** the campaign is done when a full pass over `unreached.md` adds no harness, meaning every remaining function has a reason code other than `no-harness`. The final T1/T2/T3 percentages and the reason-code breakdown go at the top of the README.

---

## Out of scope, with the hook left for it

- **Rust ports and C-to-Rust equivalence.** Each T2 postcondition and each contract in `Tools/proofs/contracts/` is the spec a later Kani harness would check a Rust port against; that is a separate plan.
- **Frama-C WP proofs.** Only needed if Task 7's spike shows CBMC loop contracts cannot be attached without source edits.
- **Other build configurations** (Windows, macOS, debug, 32-bit). Each is a new compile database and a re-run; lines under other `#ifdef` branches stay `platform-ifdef`.
- **Trace validation of the QSBR TLA+ model** against the running C.

## Self-Review

- Spec coverage: "generate proofs" — Tasks 3, 6, 7, 8, 10. "cover 100% or as close as possible" — Task 10 with its stop condition, and `unreached.md` for the remainder. "proven with a code coverage tool" — Tasks 2 and 4. "verified to be correct on the c code" — harnesses compile unmodified sources with the build's own flags (Tasks 1, 3), stale-build refusal (Task 3), CI re-run on change (Task 9), audit gate (Task 5).
- Review Focus: all five have named tests in Tasks 3, 4, 5 and 6.
- Type consistency: `Result` (Task 3) is reused by Tasks 4, 5 and 8; `Verdict.audited_tier` (Task 5) is what Task 4 reads; `model_generic_call` (Task 6) is the name used in Task 10 wave 6.
- Known soft spot: Task 7 depends on a spike whose outcome is not known; the task says what to do in both cases.
