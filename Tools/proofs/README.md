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
  `__CPROVER_assert()`. The compile database defines `NDEBUG`, so `assert()`
  checks nothing: the runner reports `error` for a harness that calls
  `assert()`, and for a T2/T3 harness with no `__CPROVER_assert()`.
- `proof.toml`:

  ```toml
  source = "Objects/longobject.c"   # repo-relative; its compile-database flags are used
  function = "v_iadd"
  tier = "T1"                       # "T1" | "T2" | "T3"
  unwind = 9                        # required for T1/T2; omit for T3
  max_size = 8
  models = []                       # Tools/proofs/models/<name>.c, linked in
  contracts = []                    # Tools/proofs/contracts/<name>.c, linked in
  disable_checks = []               # only "--unsigned-overflow-check", "--conversion-check"
  disable_reason = ""               # required when disable_checks is not empty
  ```

`python3 Tools/proofs/new_harness.py <source> <function>` creates a T1 skeleton.

`python3 Tools/proofs/run.py [--only <glob>] [--jobs N]` runs every harness:
`goto-cc` with the source's compile-database flags, then `cbmc --function harness`
with the fixed check list, then (on success) `cbmc --cover location` to record the
covered lines. A harness whose final line is unreachable (contradictory assumptions)
fails as vacuous. Each result is written to `Tools/proofs/out/<harness>/result.json`.
The runner refuses a compile database older than `build/proofs/pyconfig.h` or
`Makefile`; rerun `build_db.sh` then.
