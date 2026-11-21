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
    """Number of assumptions, counted as the audit lists them (see assumes());
    0 for a run against substituted sources or one that failed to compile."""
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


def _check_assertions(files, tier, hdir):
    """Refuse harnesses whose checks would silently vanish (``files``: harness code)."""
    found = False
    for f in files:
        code = _COMMENT_OR_STRING.sub(" ", pathlib.Path(f).read_text(errors="replace"))
        if _PLAIN_ASSERT.search(code):
            raise HarnessError(f"{os.path.relpath(f, hdir)} calls assert(), which NDEBUG "
                               "compiles out; use __CPROVER_assert(cond, \"description\")")
        found = found or "__CPROVER_assert" in code
    if tier in ("T2", "T3") and not found:
        raise HarnessError(f"tier {tier} needs a postcondition, but the harness code "
                           "has no __CPROVER_assert")


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


GOTO_PLACEHOLDER = "harness.goto"


def _dependencies(cmd, cwd, resolve=True):
    """Every file the preprocessor reads for the translation units of ``cmd``
    (``goto-cc -M`` with the same flags), as real paths, or with ``resolve``
    false as absolute paths with symlinks left in place."""
    dep = [a for a in cmd if a not in ("-o", GOTO_PLACEHOLDER)]
    dep.insert(1, "-M")
    r = subprocess.run(dep, cwd=cwd, capture_output=True, text=True, errors="replace",
                       timeout=TIMEOUT)
    if r.returncode != 0:
        err = [l for l in r.stderr.splitlines() if "error" in l.lower()] or r.stderr.splitlines()
        raise HarnessError("goto-cc -M failed: " + (err[0].strip() if err else f"exit {r.returncode}")[:300])
    words = r.stdout.replace("\\\n", " ").split()
    paths = {os.path.normpath(os.path.join(cwd, w)) for w in words if not w.endswith(":")}
    return {os.path.realpath(p) for p in paths} if resolve else paths


def inside(path, directory):
    """Whether ``path`` (resolved) lies below ``directory`` (resolved)."""
    return os.path.realpath(path).startswith(os.path.realpath(directory) + os.sep)


@functools.cache
def system_include_dirs():
    """The compiler's own #include search directories, as real paths.

    goto-cc preprocesses with ``gcc -E``, so gcc is asked: ``gcc -E -Wp,-v -xc
    /dev/null`` prints the search list on stderr.
    """
    try:
        r = subprocess.run(["gcc", "-E", "-Wp,-v", "-xc", os.devnull], capture_output=True,
                           text=True, errors="replace", timeout=60,
                           env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HarnessError(f"gcc -E -Wp,-v failed: {exc}") from None
    dirs, listing = [], False
    for line in r.stderr.splitlines():
        if line.startswith("#include ") and line.endswith("search starts here:"):
            listing = True
        elif line.startswith("End of search list"):
            listing = False
        elif listing and line.startswith(" "):
            dirs.append(os.path.realpath(line.strip()))
    if r.returncode != 0 or not dirs:
        raise HarnessError("gcc -E -Wp,-v printed no #include search list")
    return tuple(dict.fromkeys(dirs))


def proof_files(hdir, cfg, compile_db=None):
    """(harness code, stub files) among the files the harness's compilation
    reads (``goto-cc -M`` with the run's flags), by real path.

    Harness code: harness.c and every file it includes from the harness
    directory, sorted.  A symlink in the directory whose target lies outside is
    not harness code.  Stub files: {path: "models/<file>" or "contracts/<file>"}
    for every file read from models/ or contracts/, listed in proof.toml or not.
    """
    cmd, cwd, _ = goto_cc_command(hdir, cfg, GOTO_PLACEHOLDER, compile_db)
    deps = _dependencies(cmd, cwd)
    code = {os.path.realpath(pathlib.Path(hdir) / "harness.c")}
    code |= {f for f in deps if inside(f, hdir)}
    stubs = {}
    for kind, base in (("models", MODELS), ("contracts", CONTRACTS)):
        base = os.path.realpath(base)
        for f in sorted(deps | set(map(os.path.realpath, _stub_sources(cfg)))):
            if f.startswith(base + os.sep) and f not in code:
                stubs[f] = f"{kind}/{os.path.relpath(f, base)}"
    return sorted(code), stubs


def harness_files(hdir, cfg, compile_db=None):
    """Harness code (see proof_files)."""
    return proof_files(hdir, cfg, compile_db)[0]


def input_hash(hdir, *, compile_db=None):
    """sha256 binding a run or audit to everything that determines its outcome.

    It covers, each file prefixed by its repo-relative path: proof.toml, every
    file inside the repository that goto-cc reads for the harness (harness.c,
    the source, every included file, listed models and contracts, as reported
    by ``goto-cc -M`` with the run's flags); the exact goto-cc arguments; the
    exact cbmc arguments of the property and cover runs; and ``cbmc --version``.

    Raises HarnessError when proof.toml is invalid or a file is missing.
    """
    hdir = pathlib.Path(hdir).resolve()
    cfg = _read_config(hdir)
    cmd, cwd, _ = goto_cc_command(hdir, cfg, GOTO_PLACEHOLDER, compile_db)
    root = os.path.realpath(ROOT) + os.sep
    files = {os.path.realpath(p) for p in
             (hdir / "harness.c", hdir / "proof.toml", ROOT / cfg["source"], *_stub_sources(cfg))}
    files |= {f for f in _dependencies(cmd, cwd) if f.startswith(root)}
    h = hashlib.sha256()

    def put(label, data):
        h.update(b"%d:%s\0%d:" % (len(label), label, len(data)))
        h.update(data)

    for path in sorted(files):
        try:
            data = pathlib.Path(path).read_bytes()
        except OSError as exc:
            raise HarnessError(f"{path} not readable: {exc.strerror}") from None
        put(os.path.relpath(path, ROOT).encode(), data)
    base = cbmc_base(GOTO_PLACEHOLDER, cfg)
    put(b"goto-cc", json.dumps([*cmd, cwd]).encode())
    put(b"cbmc", json.dumps([[*base, *checks(cfg), "--json-ui"],
                             [*base, "--cover", "location", "--json-ui"]]).encode())
    put(b"cbmc --version", inventory.cbmc_version().encode())
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


def strip_comments(text):
    """Blank out comments and string/char literals, keeping every newline."""
    return _COMMENT_OR_STRING.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), text)


def assume_texts(path):
    """[(line, "__CPROVER_assume(...)")] for every literal assume in ``path``."""
    raw = pathlib.Path(path).read_text(errors="replace")
    code = strip_comments(raw)
    out = []
    for m in re.finditer(r"__CPROVER_assume\s*\(", code):
        depth, i = 1, m.end()
        while i < len(code) and depth:
            depth += {"(": 1, ")": -1}.get(code[i], 0)
            i += 1
        out.append((code.count("\n", 0, m.start()) + 1, " ".join(raw[m.start():i].split())))
    return out


def goto_listing(goto, cwd, timeout=TIMEOUT):
    """``cbmc --show-goto-functions`` of the harness: every function with a
    body, after cbmc links its library and removes function pointers."""
    cmd = ["cbmc", str(goto), "--function", ENTRY, "--show-goto-functions"]
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       errors="replace", timeout=timeout)
    if r.returncode != 0:
        tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or [""]
        raise HarnessError(f"cbmc --show-goto-functions exit {r.returncode}: {tail[0][:200]}")
    return r.stdout


_ASSUME = re.compile(r"\s+((?:[\d, ]+: )?)ASSUME (.*)$")
_ASSERT = re.compile(r"\s+(?:[\d, ]+: )?ASSERT (.*?)(?: // .*)?$")
_COMMENT = re.compile(r"\s+// \d+ (.*)$")
_LOCATION = re.compile(r"file (.+?) line (\d+)(?: function \S+)?$")
NO_INSTRUCTION = "[no ASSUME instruction on this line]"


def _source_line(path, line):
    try:
        return " ".join(pathlib.Path(path).read_text(errors="replace").splitlines()[line - 1].split())
    except (OSError, IndexError):
        return "?"


def assumes(listing, cwd, hdir, code, stubs):
    """(assumes, stray): every assumption of the proof, as "<file>:<line>: <text>".

    One entry per ASSUME instruction of the goto program (``listing``) that is
    located in a source file, so an assume written through a macro counts where
    the macro is used.  The text is the literal ``__CPROVER_assume(...)`` on that
    line if there is one, else the source line and the instruction's condition.
    Literal assumes in harness code (``code``) and stub files (``stubs``) that
    no instruction is located at (a macro body, say) are listed too, marked.
    ``stray`` holds "<file>:<line>" for instructions located outside harness
    code and stub files, or with no location.  Left out: CBMC's built-in
    library, whose locations name no real file; and an ASSUME that directly
    follows an ASSERT of the same condition and is no jump target, since that
    assertion is a property of the proof (CBMC writes ``ASSERT false; ASSUME
    false`` after the candidates of a call through a function pointer).
    """
    hdir = os.path.realpath(hdir)
    labels = {f: os.path.relpath(f, hdir) for f in code}
    labels.update(stubs)
    rank = {f: n for n, f in enumerate(labels)}
    literal = {f: assume_texts(f) for f in labels}
    root = os.path.realpath(ROOT) + os.sep
    entries, stray = [], []
    lines = listing.splitlines()
    for i, text in enumerate(lines):
        m = _ASSUME.match(text)
        if m is None:
            continue
        before = _ASSERT.match(lines[i - 2]) if i >= 2 else None
        if before is not None and before.group(1) == m.group(2) and not m.group(1):
            continue
        comment = _COMMENT.match(lines[i - 1]) if i else None
        loc = _LOCATION.match(comment.group(1)) if comment else None
        if loc is None:
            stray.append("(no source location)")
            entries.append((len(rank), 0, f"(no source location): [ASSUME {m.group(2)}]"))
            continue
        path, line = os.path.realpath(os.path.join(cwd, loc.group(1))), int(loc.group(2))
        if loc.group(1).startswith("<") and not os.path.exists(path):
            continue                                    # <builtin-library-...>
        hit = next((t for t in literal.get(path, ()) if t[0] == line), None)
        if hit is not None:
            literal[path].remove(hit)
            text = hit[1]
        else:
            text = f"{_source_line(path, line)} [ASSUME {m.group(2)}]"
        if path in labels:
            label = labels[path]
        else:
            label = os.path.relpath(path, ROOT) if path.startswith(root) else path
            stray.append(f"{label}:{line}")
        entries.append((rank.get(path, len(rank)), line, f"{label}:{line}: {text}"))
    for f, rest in literal.items():
        entries += [(rank[f], line, f"{labels[f]}:{line}: {text} {NO_INSTRUCTION}")
                    for line, text in rest]
    entries.sort(key=lambda e: e[:2])
    return [e[2] for e in entries], stray


def goto_cc_command(hdir, cfg, goto, compile_db=None, include_first=()):
    """(goto-cc command, cwd, root) that compiles the harness into ``goto``."""
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
    return cmd, cwd, root


def compile_harness(hdir, cfg, goto, compile_db=None, include_first=(), timeout=TIMEOUT):
    """goto-cc harness.c and its listed models/contracts into ``goto``.

    Returns (cwd, root): the directory cbmc must run in (paths in its output
    are relative to it) and the directory covered lines are reported against.
    """
    cmd, cwd, root = goto_cc_command(hdir, cfg, goto, compile_db, include_first)
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       errors="replace", timeout=timeout)
    if r.returncode != 0:
        err = [l for l in r.stderr.splitlines() if "error" in l.lower()] or r.stderr.splitlines()
        raise HarnessError("goto-cc failed: " + (err[0].strip() if err else f"exit {r.returncode}")[:300])
    return cwd, root


def checks(cfg):
    """The fixed check list minus the checks proof.toml disables."""
    disabled = set(cfg.get("disable_checks", []))
    return [c for c in CHECKS if c not in disabled]


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
    code, stubs = proof_files(hdir, cfg, compile_db)
    _check_assertions(code, cfg["tier"], hdir)

    # A run against substituted sources (include_first) is not a run of the
    # harness's recorded inputs, so it carries no input hash.  Otherwise the
    # hash is taken before and after compiling, and recorded only if the two
    # agree: the binary was then built from exactly the hashed inputs.
    before = None if include_first else input_hash(hdir, compile_db=compile_db)
    with tempfile.TemporaryDirectory() as tmp:
        goto = os.path.join(tmp, "harness.goto")
        cwd, root = compile_harness(hdir, cfg, goto, compile_db, include_first, timeout)
        if before is not None:
            if input_hash(hdir, compile_db=compile_db) != before:
                raise HarnessError("inputs changed during run")
            result.input_hash = before
            # The same count as the audit's ``assumes`` list.
            listing = goto_listing(goto, cwd, timeout)
            result.assumes = len(assumes(listing, cwd, hdir, code, stubs)[0])

        base = cbmc_base(goto, cfg)
        result.cbmc_flags = [*base[4:], *checks(cfg)]
        code, msgs = _cbmc_json([*base, *checks(cfg), "--json-ui"], cwd, timeout)
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
