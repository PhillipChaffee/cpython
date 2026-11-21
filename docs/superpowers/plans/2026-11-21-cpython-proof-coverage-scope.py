"""Prove that the proof-coverage plan assigns every tracked C file to a wave.

Run from the repository root:

    python3 docs/superpowers/plans/2026-11-21-cpython-proof-coverage-scope.py

Each tracked ``.c``/``.h`` file is matched against RULES in order; the first
match wins.  The script prints lines of C per bucket and exits 1 if any file
is unassigned, so the plan cannot silently leave code out.
"""

import fnmatch
import subprocess
import sys
from collections import defaultdict

# (bucket, patterns).  "X:" buckets are outside the proof denominator.
RULES = [
    ("X: vendored, verified upstream in F*", ["Modules/_hacl/*"]),
    ("X: vendored third-party", [
        "Modules/expat/*", "Modules/_decimal/libmpdec/*",
        "Objects/mimalloc/*", "Include/internal/mimalloc/*",
        "Modules/_ctypes/darwin/*", "Modules/_ctypes/libffi*",
    ]),
    ("X: other platforms", [
        "PC/*", "PCbuild/*", "Mac/*", "iOS/*", "Android/*", "Apple/*",
        "Platforms/*", "Python/dynload_win.c", "Python/dynload_hpux.c",
        "Python/emscripten_*", "Modules/_wmi*", "Modules/_winapi.c",
        "Modules/overlapped.c", "Modules/_scproxy.c", "Python/thread_nt.h",
        "Modules/expat/*", "Python/asm_trampoline*",
    ]),
    ("X: JIT machine-code emission", [
        "Python/jit*.c", "Python/perf_jit_trampoline.c", "Tools/jit/*",
        "Include/internal/pycore_jit*.h",
    ]),
    ("X: test, demo and example code", [
        "Modules/_test*", "Modules/_xx*", "Modules/xx*", "Lib/test/*",
        "Doc/*", "Tools/*", "Modules/_ctypes/_ctypes_test*",
        "Programs/_test*", "Programs/test_*", "Programs/_freeze_module.c",
        "Programs/_bootstrap_python.c", "Programs/check_*",
        "Modules/getpath_noop.c", "Python/frozen_modules/*", "Lib/*",
    ]),
    ("1: pure leaves", [
        "Python/pyctype.c", "Python/mystrtoul.c", "Python/pystrhex.c",
        "Python/pyhash.c", "Python/pystrcmp.c", "Python/pymath.c",
        "Python/dtoa.c", "Python/pystrtod.c", "Objects/bytes_methods.c",
        "Objects/stringlib/*",
    ]),
    ("2: value objects", [
        "Objects/longobject.c", "Objects/floatobject.c",
        "Objects/complexobject.c", "Objects/boolobject.c",
        "Objects/bytesobject.c", "Objects/bytearrayobject.c",
        "Objects/tupleobject.c", "Objects/rangeobject.c",
        "Objects/sliceobject.c",
    ]),
    ("3: containers and text", [
        "Objects/listobject.c", "Objects/dictobject.c", "Objects/setobject.c",
        "Objects/unicodeobject.c", "Objects/unicodectype.c",
        "Objects/memoryobject.c", "Objects/unicodetype_db.h",
        "Objects/listsort.txt",
    ]),
    ("4: front end", ["Parser/*"]),
    ("5: compiler", [
        "Python/symtable.c", "Python/compile.c", "Python/codegen.c",
        "Python/flowgraph.c", "Python/assemble.c", "Python/ast*.c",
        "Python/Python-ast.c", "Python/instruction_sequence.c",
        "Python/future.c",
    ]),
    ("6: interpreter core", [
        "Python/bytecodes.c", "Python/generated_cases.c.h",
        "Python/executor_cases.c.h", "Python/ceval.c", "Python/ceval_macros.h",
        "Python/frame.c", "Objects/frameobject.c", "Objects/codeobject.c",
        "Objects/funcobject.c", "Objects/genobject.c", "Objects/call.c",
        "Python/specialize.c", "Python/optimizer*", "Python/opcode_targets.h",
        "Python/intrinsics.c", "Python/instrumentation.c",
    ]),
    ("7: object model and memory", [
        "Objects/object.c", "Objects/typeobject.c", "Objects/abstract.c",
        "Objects/descrobject.c", "Objects/classobject.c",
        "Objects/exceptions.c", "Objects/weakrefobject.c",
        "Objects/obmalloc.c", "Python/gc*.c", "Python/slots*.c",
        "Objects/typeslots.inc",
    ]),
    ("8: runtime, remaining Python/ Objects/ Programs/", [
        "Python/*", "Objects/*", "Programs/*",
    ]),
    ("9: extension modules", ["Modules/*"]),
    ("H: public and internal headers (proved through including harnesses)",
     ["Include/*"]),
    ("X: build configuration probes", ["pyconfig.h.in", "Misc/platform_triplet.c"]),
]


def bucket_for(path):
    for bucket, patterns in RULES:
        if any(fnmatch.fnmatch(path, p) for p in patterns):
            return bucket
    return None


def main():
    files = subprocess.run(
        ["git", "ls-files", "*.c", "*.h"],
        check=True, capture_output=True, text=True,
    ).stdout.split()
    lines = defaultdict(int)
    count = defaultdict(int)
    unassigned = []
    for path in files:
        bucket = bucket_for(path)
        if bucket is None:
            unassigned.append(path)
            continue
        with open(path, "rb") as f:
            lines[bucket] += sum(1 for _ in f)
        count[bucket] += 1

    total = sum(lines.values())
    in_scope = sum(n for b, n in lines.items() if not b.startswith("X:"))
    print(f"{'bucket':<70} {'files':>6} {'lines':>9}")
    for bucket, _ in RULES:
        if bucket in lines:
            print(f"{bucket:<70} {count[bucket]:>6} {lines[bucket]:>9}")
    print(f"\ntracked C files: {len(files)}  assigned: {len(files) - len(unassigned)}")
    print(f"total lines: {total}  in proof scope: {in_scope} "
          f"({100 * in_scope / total:.1f}%)")
    if unassigned:
        print(f"\nUNASSIGNED ({len(unassigned)}):")
        print("\n".join(unassigned))
        return 1
    print("every tracked C file is assigned to a wave or a named exclusion")
    return 0


if __name__ == "__main__":
    sys.exit(main())
