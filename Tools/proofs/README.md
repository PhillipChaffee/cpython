# Formal proofs for CPython (CBMC)

## Setup

Ubuntu 24.04 is required (`install.sh` uses apt).

From the repository root:

```sh
Tools/proofs/install.sh      # installs cbmc, bear, lcov, universalmutator at the versions in versions.env
Tools/proofs/build_db.sh     # builds CPython under bear -> build/proofs/compile_commands.json
python3 -m unittest discover -s Tools/proofs/tests -t Tools/proofs -v
```

## Denominator

`inventory.py` lists every executable C line and function that proofs could cover
(the denominator for later coverage ratios). For each translation unit in the
compile database it runs `goto-cc` with that entry's `-I/-D/-U/-std` flags, then
`cbmc --cover location --show-properties --json-ui` and unions the
`basicBlockLines` of every coverage goal (all functions, reachable or not).
Files matching `exclude` in `scope.toml` (vendored, other-platform, JIT, test and
demo code) are skipped. Per-file results are cached in `Tools/proofs/out/cache/`.

```sh
python3 Tools/proofs/inventory.py --out Tools/proofs/out/inventory.json [--jobs N] [--no-cache] [--allow-errors]
```

Only successful per-file results are cached (errors are retried every run). The
cache key is the source path, its mtime and size, the flags and the `cbmc
--version` string; it does not track headers, so after editing a header run with
`--no-cache` (or delete `Tools/proofs/out/cache/`).

Totals on this tree (4 cores, about 5.5 minutes cold, under a second cached):

| files | functions | lines | errors |
|------:|----------:|------:|-------:|
| 499   | 15846     | 254223 | 17    |

The command exits 1 when any file could not be analysed, unless `--allow-errors`
is given. **The 17 files that failed are not in the denominator.** Any coverage
percentage computed from it must list them as "size unknown, uncovered" until they
are resolved or explicitly waived. They are recorded under `"errors"` in the JSON:
14 files fail in `goto-cc` on glibc's `readdir64` (`_posixsubprocess`,
`_remote_debugging/*`, `posixmodule`, `remote_debugging`; `_FILE_OFFSET_BITS=64`
from `pyconfig.h` redirects `readdir`, and no command-line flag overrides the
`__REDIRECT` handling), `Python/pystrhex.c` fails on `__builtin_shufflevector`, and
`Modules/mathmodule.c` hits a `goto-cc` invariant violation.

## Harnesses

A harness is a directory `harnesses/<source>/<function>/` with two files:

- `harness.c` defines `void harness(void)`. It `#include`s the source file (so
  static functions are callable), declares the arguments as uninitialised
  locals (any value), narrows them with `__CPROVER_assume()` only as far as the
  callers guarantee, calls the function and states postconditions with
  `__CPROVER_assert()`. Control flow in `harness()` that skips the target
  call on some inputs (an early `return`, a `goto`, or a condition around
  the call) restricts the inputs exactly like an assume, and the audit does
  not detect it: reviewers must treat it as one. Harnesses call the target
  unconditionally. The compile database defines `NDEBUG`, so `assert()`
  checks nothing: the runner reports `error` for a harness that calls
  `assert()`, and for a T2/T3 harness with no `__CPROVER_assert()`. It checks
  all harness code (see "Audit"), not only harness.c. `assumes` in `result.json`
  is the length of the audit's `assumes` list (see "Audit").
- `proof.toml`:

  ```toml
  source = "Objects/longobject.c"   # repo-relative; its compile-database flags are used
  function = "v_iadd"
  tier = "T1"                       # "T1" | "T2" | "T3"
  unwind = 9                        # required for T1/T2; omit for T3
  max_size = 8
  object_bits = 12                  # optional: cbmc --object-bits (more objects; checks nothing less)
  models = []                       # Tools/proofs/models/<name>.c (linked in, if it exists) and <name>.h
  contracts = []                    # Tools/proofs/contracts/<name>.c (linked in, if it exists) and <name>.h
  # enforce_contract = "v_iadd"     # check the target's contract (see "Contracts")
  # replace_contracts = ["v_iadd"]  # replace calls by enforced contracts (see "Contracts")
  disable_checks = []               # only "--unsigned-overflow-check", "--conversion-check"
  disable_reason = ""               # required when disable_checks is not empty
  # Audit only (see "Audit"):
  # havoc_callees = ["name"]        # body-less callees the proof may reach
  # havoc_reason = "..."            # required with havoc_callees
  # harness_stubs = ["name"]        # functions defined in harness.c that the target reaches
  # harness_stubs_reason = "..."    # required with harness_stubs
  # predefine = ["NAME"]            # macros harness code may #define/#undef before the source
  # predefine_reason = "..."        # required with predefine
  # mutants_max = 30                # mutants run for T2/T3
  # [[equivalent_mutants]]          # offset = N, diff = "...", reason = "..."
  ```

`python3 Tools/proofs/new_harness.py <source> <function>` creates a T1 skeleton.

`python3 Tools/proofs/run.py [--only <glob>] [--jobs N]` runs every harness:
`goto-cc` with the source's compile-database flags, then `cbmc --function harness`
with the fixed check list, then (on success) `cbmc --cover location` to record the
covered lines. A harness whose final line is unreachable (contradictory assumptions)
fails as vacuous. Each result is written to `Tools/proofs/out/<harness>/result.json`.
The fixed check list includes `--unwinding-assertions`,
`--no-self-loops-to-assumptions` and `--malloc-may-fail --malloc-fail-null`.
Without the last two, CBMC's `malloc` never fails and every proof silently
assumes allocation succeeds; they are passed to every cbmc run (cover runs
too) and to goto-instrument in contract mode. A harness's own input buffers
(an argument array the caller already has) are not allocations of the code
under proof: harnesses write `__CPROVER_assume(buf != NULL)` after such a
`malloc`, and the assume is listed like any other. Without the second, CBMC turns a loop such as
`for (;;);` into an assumption that no listing shows; with it, such a loop
fails its unwinding assertion. Every gcc, goto-cc and cbmc subprocess of the
runner and the audit gets the caller's environment without `CPATH`,
`C_INCLUDE_PATH`, `CPLUS_INCLUDE_PATH`, `OBJC_INCLUDE_PATH`, `GCC_EXEC_PREFIX`,
`COMPILER_PATH`, `LIBRARY_PATH`, `DEPENDENCIES_OUTPUT` and
`SUNPRO_DEPENDENCIES`, so those cannot add include directories. The compiler
flags come only from the compile database; proof.toml has no key that adds a
flag such as `-D` or `-U`.
The runner refuses a compile database older than `build/proofs/pyconfig.h` or
`Makefile`; rerun `build_db.sh` then.

## Audit

`python3 Tools/proofs/audit.py [--only <glob>] [--jobs N] [--fail-fast]` decides,
per harness, whether a passing proof can be trusted and which tier it has earned.
It writes `Tools/proofs/out/<harness>/audit.json` with `ok`, `audited_tier`,
`problems`, `stubs`, `assumes` (every assumption, with its file and line; see
"Assumes" below), `mutants_total`, `mutants_killed`, `mutants_vacuous`,
`mutants_error`, `unused_equivalents` and `input_hash`, prints one
line per harness and exits 1 if any harness is not `ok`. When `ok` is false the
harness counts for nothing. When `ok` is true, `audited_tier` is the declared
tier, or T1 if rule 4 or 5 is not met; such a demotion is listed in `problems`
but is not a failure.

The audit is bound to its inputs. `run.input_hash()` hashes proof.toml and
every file inside the repository that goto-cc reads for the harness, as
`goto-cc -M` with the run's flags reports them. That includes harness.c, the
source, every included file such as `Python/pyctype.c`, the headers and
`build/proofs/pyconfig.h`, and the listed models and contracts. It also hashes
the exact goto-cc and cbmc argument lists and `cbmc --version`. System headers
outside the repository are not hashed. The runner hashes the inputs before and
after compiling, and records the hash only if the two agree. Otherwise the run
is an error ("inputs changed during run"). The audit reuses `result.json` only if
its `input_hash` matches; otherwise it runs the harness again first.

1. **Checks.** The property run used `--unwinding-assertions`, an `--unwind`
   bound (below T3) and every check of the fixed list, except checks disabled
   with a non-empty `disable_reason`. The audit reads the flags the run recorded
   in `result.json` (`cbmc_flags`).
2. **Not vacuous.** *Harness code* is every file in the harness directory:
   harness.c and any header or `.c` file it includes from that directory. The
   end of `harness()` must be reachable (the runner's guard). Every assertion
   located in harness code must also be reachable, however it is written
   (directly, through a macro, in a helper function or in a header). `#line`
   directives in harness code are rejected, because they move goals to
   other files. The audit
   checks the second with `cbmc --cover assertion`: an assertion goal that
   cannot be satisfied is an assertion that no input reaches. As a cross-check,
   each literal `__CPROVER_assert(` line in harness code must have a goal.
3. **Stubs.** Every file under `models/` or `contracts/` that the compilation
   reads must be listed in proof.toml. The audit takes the files from
   `goto-cc -M` (so a header that only defines macros counts) and from the goto
   binary's symbol table. An entry `name` in `models` covers exactly
   `models/name.c`, which the runner compiles and links, and `models/name.h`,
   its header. The same holds for `contracts`. Any other file there, including
   one in a subdirectory, is unlisted and makes `ok` false.
   Every function that the proof reaches but that has no body, even after CBMC
   links its own library, must be listed in `havoc_callees`, with a
   `havoc_reason`. CBMC lets such a function return any value and does nothing
   else. That is sound for T1 memory safety of the caller only if the real
   function has no side effects through pointers (it writes no memory the
   caller can see and frees nothing). To find what is reached, the audit gives
   each body-less function an `assert(false)` body (`goto-instrument
   --generate-function-body ... assert-false`) and asks `cbmc --cover assertion`
   which of those assertions can be reached. So a call on an infeasible path does
   not count. A function defined in harness code (other than `harness()`) that the
   target function can call, directly or through other functions, is a stub
   too. It must be listed in `harness_stubs`, with a `harness_stubs_reason`.
   Helpers that only `harness()` calls are not stubs. `stubs` lists
   `models/<name>.c`, `contracts/<name>.c`, `havoc:<name>` and
   `harness:<name>`.
   A harness's compilation may read only these files (from `goto-cc -M`):
   - harness code (by real path, so a symlink in the harness directory that
     points outside is refused);
   - `models/` and `contracts/` (listed, as above);
   - repository files outside `Tools/proofs/`;
   - files under the compiler's system include directories. goto-cc
     preprocesses with `gcc -E`, so the audit asks gcc for them once:
     `gcc -E -Wp,-v -xc /dev/null` (`run.system_include_dirs()`).
   For a fixture whose `source` is under `tests/fixtures/`, that source and the
   files beside it are allowed too. Any other file makes `ok` false, because no
   rule would check it and, outside the repository, `input_hash` does not cover
   it: another harness's directory, a shared helper under `Tools/proofs/`, or
   a file outside the repository such as `/tmp/helper.h`. Every file is judged
   by its real path. A file reached through the harness directory (also by real
   path, so a symlinked directory on the way does not hide it) whose target is
   outside is reported as a symlink.

   **Assumes.** `assumes` has one entry for each `ASSUME` instruction of the
   goto program (`cbmc --show-goto-functions`) that is located in a source
   file. So an assume written through a macro is counted on the line that uses
   the macro. The entry is `<file>:<line>: __CPROVER_assume(...)` when that
   text is on the line, else `<file>:<line>: <source line> [ASSUME
   <condition>]`. A literal `__CPROVER_assume(` in harness code or in a model or
   contract file (`.c` or `.h`) with no instruction on its line, such as the
   body of a `#define`, is listed too, marked `[no ASSUME instruction on this
   line]`. Two kinds of `ASSUME` are left out: those of CBMC's built-in
   library, and one that directly follows an `ASSERT` of the same condition and
   is not a jump target. The assertion is then a property the proof checks, so
   the assume adds nothing. CBMC writes `ASSERT false; ASSUME false` this way
   after the candidates of a call through a function pointer. An `ASSUME` located anywhere
   else makes `ok` false: for example in the source under proof, where a
   harness macro that expands inside the source would put it. The runner
   computes the same list and stores its length as `assumes` in `result.json`.

   A call that does not return cuts a path off just as an assume does, so it is
   listed too, as `<file>:<line>: <source line> [CALL abort: does not return]`.
   This covers every `CALL` instruction located in harness code or in a model
   or contract file (so also one that a harness macro produces) to `abort`,
   `exit`, `_Exit`, `_exit`, `quick_exit`, `__builtin_trap`, `longjmp` and the
   other names in `run.NORETURN`, and to any function of CBMC's built-in
   library whose model contains an assumption. A call from there to another
   function that can reach one of these is listed as `[CALL f: can reach
   abort]`. Two kinds of callee are excepted: the function under proof, and
   functions defined in harness code or in a model or contract file, whose own
   calls are listed. Calls located in the CPython source are not listed: a
   `Py_FatalError()` path there is behaviour under proof.

   **Macros before the code under proof.** A `#define` or `#undef` in harness
   code that the preprocessor meets before the last `#include` of a file
   outside harness code, `models/` and `contracts/` can change the meaning of
   that file with no assume at all (`#define if(x) ...`, or a redefined
   CPython macro). It makes `ok` false, naming the macro, unless proof.toml
   lists the name in `predefine`, with a `predefine_reason`. The audit follows
   `#include "..."` and `#include <...>` into harness code and into model and
   contract headers, resolving them as gcc does (the including file's
   directory for `"..."`, then the `-I` directories, then the system ones), so
   a macro defined in such a header counts. `#import` and `#include_next` are
   refused anywhere in harness code (`ok` false, naming the line): neither is
   followed. Conditionals are not evaluated: a
   directive in a skipped `#if` branch counts too. `#pragma` (except `once`)
   and `_Pragma` in that region are always refused. A harness header that is
   included before the source should use `#pragma once`, not a `#define`
   include guard. An unused `predefine` entry is reported as a note.
4. **Mutation (T2; T3 once T3 mode exists).** universalmutator (`mutate --noCheck --lines`)
   mutates only the target function's lines, as CBMC's source locations give
   them. Each mutant is a full copy of the source file in a temporary tree with
   the same repo-relative path. The harness is compiled with that tree first on
   the include path. The real source tree is never written. Mutants that goto-cc
   rejects are discarded. At most `mutants_max` (default 30) are run, chosen in a
   fixed-seed shuffled order, in parallel. Each run is limited to 3 times the
   baseline proof time (at least 10 s) per goto-cc or cbmc call. A mutant is
   killed only when one of its properties fails. A mutant whose proof is
   vacuous (for example, it calls `abort()` on every input), errors or times
   out is not killed. It is counted in `mutants_vacuous` or `mutants_error`
   and always demotes the harness. A mutant whose proof passes demotes the
   harness to T1 unless proof.toml lists it as equivalent, with the offset
   and diff printed in its problem:

   ```toml
   # from "surviving mutant (line 83, offset 9): if (len == 0) ==> if (len <= 0)"
   [[equivalent_mutants]]
   offset = 9                          # mutated line minus the function's first code line
   diff = "if (len == 0) ==> if (len <= 0)"
   reason = "len >= 0, so len <= 0 is len == 0"
   ```

   An entry that matches no mutant in the sample is listed in the verdict's
   `unused_equivalents` (a note, not a problem), so a stale offset is visible.
   Below T2, and for T3 until T3 mode exists, no mutants are sampled, so
   every entry is listed there.
   A target guarded by `abort()` or `Py_FatalError()` may be capped at T1. A
   mutant that makes the guard fire on every input is vacuous, and a vacuous
   mutant counts as not killed.

   List a mutant only if it changes nothing a caller can observe under the
   harness's preconditions. A mutant that differs only for inputs beyond the
   proof bound is not equivalent: it is a limit of the bound. `--fail-fast`
   stops at the first mutant that is not killed.
5. **T3.** T3 needs no `unwind` and a contract-mode run with loop contracts
   (`--apply-loop-contracts`, Task 7). The runner enforces function contracts
   (see "Contracts") but has no unbounded mode yet, so every T3 harness is
   demoted to T1 for now.

## Models

`models/` holds sound over-approximations of CPython runtime functions that
harnesses link instead of the real ones. Each function's contract is the
comment above it. List each file used in proof.toml `models`; `models.h`
(the entry `"models"`) declares them. Include it after the source.

| File | Provides | Narrower than the real function |
| --- | --- | --- |
| `alloc.c` | `PyMem_*`, `PyMem_Raw*`, `PyObject_*` Malloc/Calloc/Realloc/Free, `_PyObject_GC_New`, `_PyObject_GC_NewVar`, `PyObject_GC_Del` | Every allocator can return NULL on any call. Blocks are exact-size, except `PyObject_*` requests under 32 bytes, which get 32 (pymalloc's size class): CBMC checks `z->long_value` against the whole member, and a 0- or 1-digit int requests 28 bytes. A read in [n, 32) of such a block is not reported. |
| `object.c` | `model_nondet_long/bytes/unicode/object()`, `_Py_NewReference` | Never NULL (an existing object). Ints: real layout, normalised, digits <= `PyLong_MASK`, at least `sizeof(PyLongObject)` bytes. Str: compact ASCII only (compact non-ASCII and legacy strings are not modelled). Objects: int, bytes, str, or a type with no slots. |
| `errors.c` | `model_current_exception`, `model_error_is_set()`, `PyErr_SetString`/`SetObject`/`Format`/`NoMemory`/`Occurred`/`Clear`, `_PyErr_SetString`, `_PyErr_BadInternalCall`, the `PyExc_*` types they raise | Starts with no error set. Exception values and tracebacks are not modelled. |
| `callee.c` | `model_generic_call()` | NULL with the error set, or a new reference to a `model_nondet_object()`. Side effects of the real callee on global state are not modelled. |
| `runtime.c` | `_PyRuntime` (real small-int table, other fields zero), `_Py_tss_tstate`, `_Py_tss_interp`, `model_init_runtime()` | Fields other than the small ints, the reference tracer (off) and the int free list hold their zero value; a harness whose target reads another one sets it itself. |

A pointer into `_PyRuntime` (a small int) read through a pointer whose
possible targets include that 251 KB object makes CBMC extract bytes at a
symbolic offset of it, which runs out of memory. Branch on
`__CPROVER_same_object(p, _PyLong_SMALL_INTS)` directly on that pointer
(CBMC then drops `_PyRuntime` from its targets in the other branch) and read
a small int by index.

## Contracts

`contracts/<file>.h` attaches CBMC contracts (`__CPROVER_requires`,
`__CPROVER_ensures`, `__CPROVER_assigns`) to functions of `<file>` without
editing it: a redeclaration of the function with the clauses, included after
the source. Attaching works for static functions too.

- `enforce_contract = "f"` (f must be the target) runs `goto-cc --function
  harness` (relink), `goto-instrument --drop-unused-functions` (`--dfcc`
  stops on the `__atomic_*` library's call to `__CPROVER_fence`, which has
  no body), then `goto-instrument --dfcc harness --malloc-may-fail
  --malloc-fail-null --enforce-contract f`: requires are assumed, ensures
  and assigns checked. The mutation gate and coverage work as usual.
- `replace_contracts = ["g", ...]` uses `--replace-call-with-contract g`:
  each call's requires are checked, its assigns havocked, its ensures
  assumed (listed in `assumes`).
- A function named in either without contract clauses is an error:
  goto-instrument would otherwise derive an empty contract (assigns nothing,
  ensures true).
- Contract clauses in harness code or a model are refused: a harness could
  otherwise replace a call by a contract that nothing enforces.
- **No orphan contracts.** A `replace_contracts` entry g needs an enforcing
  harness (`enforce_contract = "g"`) whose `audit.json` is `ok`, current
  (its `input_hash` equals `run.input_hash()` of it now), and records in
  `contract_hashes` the current sha256 of every listed contract file that
  declares g's clauses. `audit.py` audits enforcing harnesses first.
- Facts of CBMC 5.95.1 found while building this: `goto-instrument --dfcc`
  needs an entry point (`goto-cc --function harness`); goto-instrument links
  CBMC's library, so `--malloc-may-fail` given only to cbmc has no effect
  on a `--dfcc` binary; an assumed `__CPROVER_forall` whose range is
  symbolic (`i < m`) is silently not assumed (asserted ones fail), so every
  quantifier needs a constant bound (`i < 3 && i < m`); goto-cc refuses a
  second contract for the same function ("contract already set").
- Cost: enforcing the `v_iadd` contract at 3 digits took about 3 to 6
  minutes against about 2 minutes for the plain proof at 4 digits
  (`pilot-report.md`).

## Reading the report

`python3 Tools/proofs/coverage.py` merges `out/inventory.json` (the denominator)
with every harness's `out/<harness>/result.json` and `audit.json` and writes, to
`out/`: `proof-coverage.json`, `proof-coverage-t1.info`, `-t2.info`, `-t3.info`
(lcov, absolute source paths) and `unreached.md`, then prints a summary.

```sh
python3 Tools/proofs/coverage.py [--fail-under-t1 PCT] [--baseline FILE] [--file PATH] [--include-unaudited]
genhtml Tools/proofs/out/proof-coverage-t1.info -o Tools/proofs/out/html   # works without extra flags
```

- **Tiers.** A line's tier is the highest *audited* tier of any harness that
  covers it. A T3 line is also a T2 and a T1 line, so T1 >= T2 >= T3. The tier
  that counts is `audited_tier` from `audit.json`, not the tier declared in
  `proof.toml`: a harness that declares T2 but whose audit finds no real
  postcondition counts at T1.
- **What counts.** Only a harness with status `pass`, an `audit.json` with
  `ok: true` and a valid `audited_tier`, whose harness directory still exists and
  whose `input_hash` in `result.json` and in `audit.json` both equal
  `run.input_hash()` of the harness now. Anything else contributes nothing and is
  listed as skipped, with the reason (`stale`, `harness deleted`, `not audited`,
  `audit failed`, `status fail`), in the summary. Covered lines outside the
  inventory are ignored. A line is one distinct (file, line) pair even when the
  inventory lists it in two functions, so `total_lines` can be a little below the
  inventory's per-function sum.
- **T2/T3 credit is for the target function only.** The target is the `source`
  and `function` in the harness's `proof.toml`. Every other line the harness
  covers counts at T1 only, because a postcondition on `f` says nothing about the
  correctness of its callees.
- **Percentages round down** to two decimals (99.996 prints as 99.99%), so a
  percentage never shows 100.00% unless every line is covered.
- **Functions.** A function counts for a tier when at least one of its lines is
  covered at that tier; the summary also gives the number with every line covered.
- **Not in the denominator.** Files the inventory could not analyse (its
  `"errors"`) have unknown size and are not in `total_lines`, so every percentage is
  an upper bound on true coverage. The summary prints "percentages are upper
  bounds" with their count, the JSON has `"upper_bound": true`,
  `proof-coverage.json` lists them under `not_in_denominator`, and `unreached.md`
  starts with them as "size unknown, uncovered".
- **`unreached.md`** lists every function with no covered line as
  `<path>:<function> — <reason>`. Reasons come from the optional
  `Tools/proofs/unreached_reasons.toml` (`["<path>"]` tables of function = code,
  `"*"` for the whole file); the default is `no-harness`. Allowed codes:
  `no-harness`, `unwind-infeasible`, `platform-ifdef`, `os-behaviour`,
  `inline-asm`, `solver-timeout`, `needs-contract`. Other codes are an error.
- **Gates.** `--fail-under-t1 PCT` and `--baseline FILE` (`{"t1": n, "t2": n,
  "t3": n}` covered-line counts; no tier may drop below it) exit 1 on failure.
  Exit 2 is a usage or input error, including an empty denominator with
  `--fail-under-t1`, a PCT outside 0 to 100 and `--file` with a gate.
- **`--include-unaudited` is provisional.** Until `audit.json` exists, every
  harness counts for nothing. This flag counts passing, unaudited harnesses at T1
  only (never higher, whatever they declare) and prints that the figures are
  provisional and unaudited. The files it writes are marked: lcov records use
  `TN:provisional_unaudited` and `unreached.md` starts with "PROVISIONAL —
  unaudited figures". Stale and deleted harnesses are still excluded. It cannot be combined with `--fail-under-t1` or
  `--baseline`.
- **`--file PATH`** prints one file's functions with covered/total lines and
  tier, least covered first.
