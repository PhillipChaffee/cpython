"""Run CBMC proof harnesses and record the source lines each passing proof covers.

A harness is a directory holding ``proof.toml`` and ``harness.c``.  The harness
defines ``void harness(void)`` and ``#include``s the source file it proves, so
static functions are reachable.  Per harness:

1. ``goto-cc`` harness.c (plus any model/contract sources) with the source
   file's compile-database flags;
2. ``cbmc --function harness --unwind N`` with the fixed check list;
3. if that verifies, ``cbmc ... --cover location`` to find the covered lines.

The result is written to ``Tools/proofs/out/<harness path>/result.json``.
"""

import argparse
import concurrent.futures
import dataclasses
import fnmatch
import functools
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import tomllib

import inventory

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "out"
HARNESSES = HERE / "harnesses"
MODELS = HERE / "models"
CONTRACTS = HERE / "contracts"
BUILD = ROOT / "build/proofs"
ENTRY = "harness"
TIMEOUT = 1800

CHECKS = (
    "--bounds-check", "--pointer-check", "--pointer-overflow-check",
    "--signed-overflow-check", "--unsigned-overflow-check", "--conversion-check",
    "--div-by-zero-check", "--undefined-shift-check", "--float-overflow-check",
    "--nan-check", "--pointer-primitive-check", "--unwinding-assertions",
)
DISABLEABLE = ("--unsigned-overflow-check", "--conversion-check")
TIERS = ("T1", "T2", "T3")


class StaleBuild(Exception):
    pass


class HarnessError(Exception):
    """A problem with the harness or toolchain, reported as status "error"."""


@dataclasses.dataclass
class Result:
    status: str                 # "pass" | "fail" | "error"
    tier: str
    covered: dict = dataclasses.field(default_factory=dict)   # path -> [line]
    assumes: int = 0
    stubs: list = dataclasses.field(default_factory=list)
    seconds: float = 0.0
    detail: str = ""
    properties_checked: int = 0
    """Every property (all check kinds, the harness's own assertions too) in a
    function the proof reached, i.e. one with a satisfied cover goal, plus
    harness(); properties of functions the harness never reaches are left out.
    0 unless the status is "pass"."""
    input_hash: str = ""
    """input_hash() of the harness when it was run ("" if it could not be computed)."""
    cbmc_flags: list = dataclasses.field(default_factory=list)
    """The check and unwind flags the property run of cbmc was given."""

    def to_json(self):
        return dataclasses.asdict(self)


def load_compile_db(build_dir=BUILD):
    """Return {absolute source path: entry}; refuse a database older than the build config."""
    build_dir = pathlib.Path(build_dir)
    db = build_dir / "compile_commands.json"
    if not db.exists():
        raise StaleBuild(f"{db} missing; run Tools/proofs/build_db.sh")
    db_mtime = db.stat().st_mtime
    for name in ("pyconfig.h", "Makefile"):
        dep = build_dir / name
        if dep.exists() and dep.stat().st_mtime > db_mtime:
            raise StaleBuild(f"{db} is older than {dep}; rerun Tools/proofs/build_db.sh")
    with open(db) as fh:
        entries = json.load(fh)
    out = {}
    for e in entries:
        src = os.path.realpath(os.path.join(e["directory"], e["file"]))
        out.setdefault(src, e)
    return out


@functools.cache
def _compile_db():
    return load_compile_db(BUILD)


def _out_dir(hdir, out):
    try:
        rel = hdir.relative_to(HARNESSES)
    except ValueError:
        rel = pathlib.Path("_fixtures") / hdir.name
    return out / rel


def _read_config(hdir):
    try:
        with open(hdir / "proof.toml", "rb") as fh:
            cfg = tomllib.load(fh)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise HarnessError(f"proof.toml: {exc}") from None
    for key in ("source", "function", "tier"):
        if not cfg.get(key):
            raise HarnessError(f"proof.toml: missing {key!r}")
    if cfg["tier"] not in TIERS:
        raise HarnessError(f"proof.toml: tier must be one of {TIERS}")
    if cfg["tier"] != "T3" and not isinstance(cfg.get("unwind"), int):
        raise HarnessError("proof.toml: 'unwind' (int) is required below T3")
    disabled = cfg.get("disable_checks", [])
    if not isinstance(disabled, list) or not all(isinstance(c, str) for c in disabled):
        raise HarnessError("proof.toml: disable_checks must be a list of strings")
    bad = [c for c in disabled if c not in DISABLEABLE]
    if bad:
        raise HarnessError(f"proof.toml: cannot disable {bad}; only {list(DISABLEABLE)}")
    if disabled and not cfg.get("disable_reason", "").strip():
        raise HarnessError("proof.toml: disable_checks needs a non-empty disable_reason")
    return cfg


def _stub_sources(cfg):
    srcs = []
    for kind, base in (("models", MODELS), ("contracts", CONTRACTS)):
        for name in cfg.get(kind, []):
            path = base / f"{name}.c"
            if not path.exists():
                raise HarnessError(f"{kind} entry {name!r}: {path} not found")
            srcs.append(path)
    return srcs


def _cbmc_json(cmd, cwd, timeout=TIMEOUT):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       errors="replace", timeout=timeout)
    if r.returncode not in (0, 10):
        tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or [""]
        raise HarnessError(f"cbmc exit {r.returncode}: {tail[0][:200]}")
    try:
        msgs = json.loads(r.stdout)
    except ValueError:
        raise HarnessError("cbmc printed no JSON") from None
    return r.returncode, msgs


def _section(msgs, key):
    for m in msgs:
        if key in m:
            return m[key]
    errors = [m.get("messageText", "") for m in msgs if m.get("messageType") == "ERROR"]
    raise HarnessError(f"cbmc output has no {key!r}" + (f": {errors[0][:200]}" if errors else ""))


_COMMENT_OR_STRING = re.compile(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\\n])*"|\'(?:\\.|[^\'\\\n])*\'', re.S)
_PLAIN_ASSERT = re.compile(r"(?<![\w])assert\s*\(")


def _check_assertions(harness_c, tier):
    """Refuse harnesses whose checks would silently vanish."""
    code = _COMMENT_OR_STRING.sub(" ", harness_c.read_text(errors="replace"))
    if _PLAIN_ASSERT.search(code):
        raise HarnessError("harness.c calls assert(), which NDEBUG compiles out; "
                           "use __CPROVER_assert(cond, \"description\")")
    if tier in ("T2", "T3") and "__CPROVER_assert" not in code:
        raise HarnessError(f"tier {tier} needs a postcondition, but harness.c has no __CPROVER_assert")


def _covered(goals, harness_c, cwd, root):
    """(covered {rel path: [lines]}, reached function names) from cover goals.

    The harness file and the proof scaffolding (harnesses/, models/,
    contracts/) are left out of the covered lines.
    """
    raw, reached = {}, set()
    harness_c = os.path.realpath(harness_c)
    scaffolding = tuple(os.path.realpath(d) + os.sep for d in (HARNESSES, MODELS, CONTRACTS))
    for g in goals:
        if g.get("status") != "satisfied":
            continue
        for path, funcs in inventory.goal_lines(g, cwd).items():
            path = os.path.realpath(path)
            reached.update(funcs)
            if path == harness_c or path.startswith(scaffolding):
                continue
            dst = raw.setdefault(path, {})
            for func, lines in funcs.items():
                dst.setdefault(func, set()).update(lines)
    lines = {p: sorted(set().union(*fs.values()))
             for p, fs in inventory.relativise(raw, root).items()}
    return dict(sorted(lines.items())), reached


def _harness_end_reached(goals, harness_c, cwd):
    """Whether the cover goal holding the last line of harness() is satisfied."""
    harness_c = os.path.realpath(harness_c)
    last, status = -1, None
    for g in goals:
        for path, funcs in inventory.goal_lines(g, cwd).items():
            if os.path.realpath(path) != harness_c or ENTRY not in funcs:
                continue
            top = max(funcs[ENTRY])
            if top > last:
                last, status = top, g.get("status")
    return status == "satisfied"


def input_hash(hdir):
    """sha256 over harness.c, proof.toml, the source file and every listed model
    and contract, each prefixed by its repo-relative path.

    Raises HarnessError when proof.toml is invalid or a file is missing.
    """
    hdir = pathlib.Path(hdir).resolve()
    cfg = _read_config(hdir)
    files = [hdir / "harness.c", hdir / "proof.toml", ROOT / cfg["source"], *_stub_sources(cfg)]
    h = hashlib.sha256()
    for path in files:
        try:
            data = pathlib.Path(path).read_bytes()
        except OSError as exc:
            raise HarnessError(f"{path} not readable: {exc.strerror}") from None
        rel = os.path.relpath(os.path.realpath(path), ROOT).encode()
        h.update(b"%d:%s\0%d:" % (len(rel), rel, len(data)))
        h.update(data)
    return h.hexdigest()


def run_harness(hdir, out=OUT, compile_db=None, include_first=(), timeout=TIMEOUT):
    """Run one harness; compile_db ({source path: entry}) defaults to build/proofs.

    include_first: directories searched for #include before all other include
    paths (the audit uses this to substitute a mutated copy of the source).
    timeout: seconds allowed for each goto-cc or cbmc call.
    """
    hdir = pathlib.Path(hdir).resolve()
    start = time.monotonic()
    result = Result(status="error", tier="")
    try:
        _run(hdir, result, compile_db, include_first, timeout)
    except (HarnessError, StaleBuild) as exc:
        result.status, result.detail = "error", str(exc)
    except subprocess.TimeoutExpired as exc:
        result.status, result.detail = "error", f"timeout after {exc.timeout}s"
    result.seconds = round(time.monotonic() - start, 2)
    dst = _out_dir(hdir, pathlib.Path(out))
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "result.json").write_text(json.dumps(result.to_json(), indent=1) + "\n")
    return result


def _count_assumes(paths):
    return sum(p.read_text(errors="replace").count("__CPROVER_assume") for p in paths)


def compile_harness(hdir, cfg, goto, compile_db=None, include_first=(), timeout=TIMEOUT):
    """goto-cc harness.c and its listed models/contracts into ``goto``.

    Returns (cwd, root): the directory cbmc must run in (paths in its output
    are relative to it) and the directory covered lines are reported against.
    """
    harness_c = hdir / "harness.c"
    stubs = _stub_sources(cfg)
    source = (ROOT / cfg["source"]).resolve()
    if not source.exists():
        raise HarnessError(f"source {cfg['source']} not found")
    entry = (_compile_db() if compile_db is None else compile_db).get(str(source))
    if entry is not None:
        flags, cwd = inventory.entry_flags(entry), entry["directory"]
    else:
        flags, cwd = [], str(ROOT)
    # Fixtures live under Tools/proofs; report their lines relative to their own directory.
    root = str(source.parent if HERE in source.parents else ROOT)
    flags = [*(f"-I{d}" for d in include_first), *flags,
             f"-I{ROOT}", f"-I{MODELS}", f"-I{CONTRACTS}", f"-I{source.parent}"]
    cmd = ["goto-cc", *flags, str(harness_c), *map(str, stubs), "-o", str(goto)]
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       errors="replace", timeout=timeout)
    if r.returncode != 0:
        err = [l for l in r.stderr.splitlines() if "error" in l.lower()] or r.stderr.splitlines()
        raise HarnessError("goto-cc failed: " + (err[0].strip() if err else f"exit {r.returncode}")[:300])
    return cwd, root


def cbmc_base(goto, cfg):
    """The cbmc command up to (not including) the check flags."""
    base = ["cbmc", str(goto), "--function", ENTRY]
    if "unwind" in cfg:
        base += ["--unwind", str(cfg["unwind"])]
    return base


def _run(hdir, result, compile_db, include_first=(), timeout=TIMEOUT):
    cfg = _read_config(hdir)
    result.tier = cfg["tier"]
    result.stubs = list(cfg.get("models", [])) + list(cfg.get("contracts", []))
    harness_c = hdir / "harness.c"
    if not harness_c.exists():
        raise HarnessError(f"{harness_c} not found")
    _check_assertions(harness_c, cfg["tier"])
    model_srcs = [MODELS / f"{n}.c" for n in cfg.get("models", [])]
    result.assumes = _count_assumes([harness_c, *model_srcs])
    result.input_hash = input_hash(hdir)

    with tempfile.TemporaryDirectory() as tmp:
        goto = os.path.join(tmp, "harness.goto")
        cwd, root = compile_harness(hdir, cfg, goto, compile_db, include_first, timeout)

        base = cbmc_base(goto, cfg)
        disabled = set(cfg.get("disable_checks", []))
        checks = [c for c in CHECKS if c not in disabled]
        result.cbmc_flags = [*base[4:], *checks]
        code, msgs = _cbmc_json([*base, *checks, "--json-ui"], cwd, timeout)
        props = _section(msgs, "result")
        failed = [p for p in props if p.get("status") != "SUCCESS"]
        if code == 10 or failed:
            result.status = "fail"
            names = [f"{p['property']}: {p.get('description', '')}" for p in failed]
            result.detail = f"{len(failed)} failed: " + "; ".join(names[:10])
            return

        _, msgs = _cbmc_json([*base, "--cover", "location", "--json-ui"], cwd, timeout)
        goals = _section(msgs, "goals")
        if not _harness_end_reached(goals, harness_c, cwd):
            # Contradictory assumptions make every property hold trivially.
            result.status = "fail"
            result.detail = "vacuous: the end of harness() is unreachable under its assumptions"
            return
        result.covered, reached = _covered(goals, harness_c, cwd, root)

    reached.add(ENTRY)
    result.properties_checked = sum(
        1 for p in props if p.get("sourceLocation", {}).get("function") in reached)
    result.status = "pass"
    result.detail = f"{result.properties_checked} properties verified"


def discover(only=None):
    dirs = sorted(p.parent for p in HARNESSES.rglob("proof.toml"))
    if only:
        dirs = [d for d in dirs if fnmatch.fnmatch(str(d.relative_to(HARNESSES)), only)]
    return dirs


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", help="glob on the harness path relative to harnesses/")
    ap.add_argument("--jobs", type=int, default=None, help="parallel harnesses (default: cpu count)")
    args = ap.parse_args(argv)
    try:
        _compile_db()
    except StaleBuild as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    dirs = discover(args.only)
    if not dirs:
        print("no harnesses matched", file=sys.stderr)
        return 1
    ok = True
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.jobs or os.cpu_count()) as pool:
        futures = {pool.submit(run_harness, d): d for d in dirs}
        for fut in concurrent.futures.as_completed(futures):
            r = fut.result()
            rel = futures[fut].relative_to(HARNESSES)
            nlines = sum(len(v) for v in r.covered.values())
            print(f"{r.status:5} {r.seconds:8.1f}s {nlines:5} lines  {rel}"
                  + (f"  ({r.detail})" if r.status != "pass" else ""))
            ok = ok and r.status == "pass"
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
