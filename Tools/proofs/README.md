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
python3 Tools/proofs/inventory.py --out Tools/proofs/out/inventory.json [--jobs N]
```

Totals on this tree (4 cores, about 5.5 minutes cold, under a second cached):

| files | functions | lines | errors |
|------:|----------:|------:|-------:|
| 499   | 15846     | 254223 | 17    |

The 17 errors are recorded under `"errors"` in the JSON: 14 files fail in `goto-cc`
on glibc's `readdir64` (`_posixsubprocess`, `_remote_debugging/*`, `posixmodule`,
`remote_debugging`), `Python/pystrhex.c` fails on `__builtin_shufflevector`, and
`Modules/mathmodule.c` hits a `goto-cc` invariant violation. They are not in the totals.
