#!/usr/bin/env bash
# Install the proof toolchain at the versions pinned in versions.env.
# Idempotent: safe to re-run.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=versions.env
. "$HERE/versions.env"

SUDO=""
if [ "$(id -u)" -ne 0 ]; then SUDO="sudo"; fi

$SUDO apt-get update -qq
$SUDO apt-get install -y \
    "cbmc=${CBMC_VERSION}" \
    "bear=${BEAR_VERSION}" \
    "lcov=${LCOV_VERSION}"

if [ "$(pip show universalmutator 2>/dev/null | sed -n 's/^Version: //p')" != "$UNIVERSALMUTATOR_VERSION" ]; then
    PIP="pip install --break-system-packages"
    if ! $PIP "universalmutator==${UNIVERSALMUTATOR_VERSION}"; then
        # The obsolete 'typing' backport required by comby fails to build on
        # modern Python; install the dependencies without it.
        $PIP --no-deps "universalmutator==${UNIVERSALMUTATOR_VERSION}" comby
        $PIP python-levenshtein tabulate loguru
    fi
fi

for tool in cbmc goto-cc goto-instrument bear lcov mutate; do
    command -v "$tool" >/dev/null || { echo "missing: $tool" >&2; exit 1; }
done
echo "toolchain ok"
