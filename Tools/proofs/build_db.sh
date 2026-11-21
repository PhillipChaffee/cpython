#!/usr/bin/env bash
# Build CPython under bear to produce build/proofs/compile_commands.json.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
mkdir -p "$ROOT/build/proofs"
cd "$ROOT/build/proofs"
../../configure
bear -- make -j"$(nproc)"
