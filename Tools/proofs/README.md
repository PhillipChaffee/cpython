# Formal proofs for CPython (CBMC)

## Setup

From the repository root:

```sh
Tools/proofs/install.sh      # installs cbmc, bear, lcov, universalmutator at the versions in versions.env
Tools/proofs/build_db.sh     # builds CPython under bear -> build/proofs/compile_commands.json
python3 -m unittest discover -s Tools/proofs/tests -t Tools/proofs -v
```
