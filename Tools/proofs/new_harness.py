"""Create a T1 proof harness skeleton: harnesses/<source>/<function>/{proof.toml,harness.c}."""

import argparse
import pathlib
import sys

HARNESSES = pathlib.Path(__file__).resolve().parent / "harnesses"

PROOF_TOML = """\
source = "{source}"
function = "{function}"
tier = "T1"
unwind = 9
max_size = 8
models = []
contracts = []
disable_checks = []
disable_reason = ""
"""

HARNESS_C = """\
/* Proof harness for {function}() in {source}.
 *
 * Including the source file makes its static functions callable here.
 * Declare each argument of {function}() as an uninitialised local (CBMC
 * treats it as any value of its type), restrict it with __CPROVER_assume()
 * only as far as every caller guarantees (cite the caller), then call
 * {function}().  State postconditions with __CPROVER_assert(): the compile
 * database defines NDEBUG, so assert() compiles to nothing.
 */
#include "{source}"

void harness(void)
{{
}}
"""


def create(source, function, base=HARNESSES):
    d = pathlib.Path(base) / source / function
    if d.exists():
        raise FileExistsError(f"{d} already exists")
    d.mkdir(parents=True)
    (d / "proof.toml").write_text(PROOF_TOML.format(source=source, function=function))
    (d / "harness.c").write_text(HARNESS_C.format(source=source, function=function))
    return d


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("source", help="repo-relative C file, e.g. Objects/longobject.c")
    ap.add_argument("function")
    args = ap.parse_args(argv)
    try:
        print(create(args.source, args.function))
    except FileExistsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
