"""Audit passing proof harnesses: decide whether each proof is trustworthy and
which tier it has earned.

Rules (see README.md, "Audit"):

1. the property run used ``--unwinding-assertions`` and every check of the
   fixed list, except checks disabled with a ``disable_reason``;
2. not vacuous: the end of ``harness()`` is reachable (run.py's guard) and
   every ``__CPROVER_assert`` in harness.c is reachable (``cbmc --cover
   assertion``);
3. every model/contract compiled in is listed in proof.toml, and every
   body-less function the proof reaches is listed in ``havoc_callees``;
4. T2/T3: mutants of the target function's lines must all fail the proof,
   else the harness is demoted to T1 (``equivalent_mutants`` excepted);
5. T3: no ``unwind`` and a contract-mode run (not implemented yet: demoted).

The verdict is written to ``Tools/proofs/out/<harness path>/audit.json``.
"""

import argparse
import concurrent.futures
import dataclasses
import json
import os
import pathlib
import random
import re
import subprocess
import sys
import tempfile
import time

import run

HERE = run.HERE
ROOT = run.ROOT
OUT = run.OUT
SEED = 20261121
MUTANTS_MAX = 30
MIN_MUTANT_TIMEOUT = 10.0       # seconds; floor for 3x a very fast baseline
UNDEFINED_BODY = "undefined function should be unreachable"


@dataclasses.dataclass
class Verdict:
    ok: bool = False
    audited_tier: str = None
    problems: list = dataclasses.field(default_factory=list)
    stubs: list = dataclasses.field(default_factory=list)
    assumes: list = dataclasses.field(default_factory=list)
    mutants_total: int = 0
    mutants_killed: int = 0
    mutants_vacuous: int = 0        # mutant proof "failed" only as vacuous: not killed
    mutants_error: int = 0          # mutant proof errored or timed out: not killed
    unused_equivalents: list = dataclasses.field(default_factory=list)
    """equivalent_mutants entries that match no sampled mutant (a note, not a problem)."""
    input_hash: str = ""

    def to_json(self):
        return dataclasses.asdict(self)


class AuditError(Exception):
    """A rule the harness fails outright (ok becomes false)."""


# -- source text helpers ---------------------------------------------------

def _strip_comments(text):
    """Blank out comments and string/char literals, keeping every newline."""
    return run._COMMENT_OR_STRING.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), text)


def assume_texts(path, label):
    """["<label>:<line>: __CPROVER_assume(...)"] for every assume in ``path``."""
    raw = pathlib.Path(path).read_text(errors="replace")
    code = _strip_comments(raw)
    out = []
    for m in re.finditer(r"__CPROVER_assume\s*\(", code):
        depth, i = 1, m.end()
        while i < len(code) and depth:
            depth += {"(": 1, ")": -1}.get(code[i], 0)
            i += 1
        text = " ".join(raw[m.start():i].split())
        out.append(f"{label}:{code.count(chr(10), 0, m.start()) + 1}: {text}")
    return out


def assert_lines(path):
    """Line numbers of ``path`` holding a literal __CPROVER_assert call (not
    #define lines).  Only a cross-check: reachability is decided per goal."""
    lines = _strip_comments(pathlib.Path(path).read_text(errors="replace")).splitlines()
    return [n for n, l in enumerate(lines, 1)
            if re.search(r"__CPROVER_assert\s*\(", l) and not l.lstrip().startswith("#")]


# -- harness code ------------------------------------------------------------

def _in_dir(path, hdir):
    return run.inside(path, hdir)


harness_files = run.harness_files


def foreign_includes(hdir, cfg, compile_db=None):
    """Problems for files the harness's compilation reads that no rule governs.

    Allowed: harness code (real path inside the harness directory), models and
    contracts (rule 3), repository files outside Tools/proofs/ and files outside
    the repository.  For a fixture source under tests/fixtures/, that source and
    the files directly beside it are allowed too.  Anything else under
    Tools/proofs/ (another harness, a shared helper) is refused, as is a symlink
    in the harness directory whose target lies outside it.
    """
    cmd, cwd, _ = run.goto_cc_command(hdir, cfg, run.GOTO_PLACEHOLDER, compile_db)
    hdir_s = str(hdir) + os.sep
    source = os.path.realpath(ROOT / cfg["source"])
    fixture_source = run.inside(source, HERE / "tests" / "fixtures")
    problems = []
    for raw in sorted(run._dependencies(cmd, cwd, resolve=False)):
        real = os.path.realpath(raw)
        if run.inside(real, hdir):
            continue
        if raw.startswith(hdir_s):
            problems.append(f"{os.path.relpath(raw, hdir)} is a symlink to {real}, outside "
                            "the harness directory; copy the helper in instead")
        elif run.inside(real, run.MODELS) or run.inside(real, run.CONTRACTS):
            continue
        elif run.inside(real, HERE):
            if fixture_source and os.path.dirname(real) == os.path.dirname(source):
                continue
            problems.append(f"includes {os.path.relpath(real, ROOT)}, proof code outside "
                            "the harness directory that no audit rule checks")
    return problems


_LINE_DIRECTIVE = re.compile(r"^[ \t]*#[ \t]*(?:line\b|\d)", re.M)


def line_directives(files, hdir):
    """Problems for #line directives (and GNU '# N' markers) in harness code:
    they relocate goals, so assertions could hide from the reachability check."""
    out = []
    for f in files:
        code = _strip_comments(pathlib.Path(f).read_text(errors="replace"))
        for m in _LINE_DIRECTIVE.finditer(code):
            line = code.count("\n", 0, m.start()) + 1
            out.append(f"{os.path.relpath(f, hdir)}:{line}: #line directives are not "
                       "allowed in harness code")
    return out


# -- rule 1 ------------------------------------------------------------------

def check_flags(cfg, result):
    """Problems with the checks the property run used."""
    problems = []
    if "--unwinding-assertions" not in run.CHECKS or "--unwinding-assertions" in run.DISABLEABLE:
        problems.append("run.py does not always pass --unwinding-assertions")
    disabled = set(cfg.get("disable_checks", []))
    if disabled and not str(cfg.get("disable_reason", "")).strip():
        problems.append("disable_checks without a disable_reason")
    flags = set(result.cbmc_flags)
    missing = [c for c in run.CHECKS if c not in disabled and c not in flags]
    if missing:
        problems.append(f"property run lacked checks {missing}")
    if cfg["tier"] != "T3" and "--unwind" not in flags:
        problems.append("property run had no --unwind bound")
    return problems


# -- goto inspection ------------------------------------------------------------

def _cbmc(cmd, cwd, timeout=run.TIMEOUT):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                       errors="replace", timeout=timeout)
    if r.returncode not in (0, 10):
        tail = (r.stderr or r.stdout).strip().splitlines()[-1:] or [""]
        raise AuditError(f"{cmd[0]} exit {r.returncode}: {tail[0][:200]}")
    return r.stdout


_FN_HEADER = re.compile(r"^(\S+) /\* \1 \*/$", re.M)
_INS_LOC = re.compile(r"^\s+// \d+ file (.+?) line (\d+)(?: function \S+)?$", re.M)
_CALL = re.compile(r"\bCALL (?:\S+ := )?([^\s(]+)\(")


@dataclasses.dataclass
class Function:
    locs: set           # {(abs path, line)} of its instructions
    calls: set          # names it calls directly (cbmc has resolved function pointers)


def goto_functions(goto, cwd):
    """{name: Function} for every function with a body, after cbmc links its
    library and replaces calls through function pointers by direct calls.
    (The JSON listing drops the location of some instructions, such as IF, so
    the text listing is parsed.)"""
    out = _cbmc(["cbmc", str(goto), "--function", run.ENTRY, "--show-goto-functions"], cwd)
    heads = list(_FN_HEADER.finditer(out))
    fns = {}
    for h, nxt in zip(heads, heads[1:] + [None]):
        body = out[h.end():nxt.start() if nxt else len(out)]
        fns[h.group(1)] = Function(
            {(os.path.realpath(os.path.join(cwd, m.group(1))), int(m.group(2)))
             for m in _INS_LOC.finditer(body)},
            {m.group(1) for m in _CALL.finditer(body)})
    return fns


def function_lines(functions, name, source):
    """Source lines of the instructions of function ``name`` located in ``source``."""
    source = os.path.realpath(source)
    fn = functions.get(name)
    return sorted(line for path, line in fn.locs if path == source) if fn else []


def reachable_from(functions, start):
    """Names of the functions ``start`` can call, directly or transitively."""
    seen, todo = set(), [start]
    while todo:
        fn = functions.get(todo.pop())
        for callee in fn.calls if fn else ():
            if callee not in seen:
                seen.add(callee)
                todo.append(callee)
    return seen


def harness_stubs(functions, hdir, target):
    """Functions defined in harness code (any file in the harness directory),
    other than harness(), that the target can reach."""
    defined = {n for n, f in functions.items()
               if n != run.ENTRY and any(_in_dir(path, hdir) for path, _ in f.locs)}
    return sorted(defined & reachable_from(functions, target))


def bodyless(goto, cwd, functions):
    """Functions called or declared in the program that have no body even after
    cbmc links its library (``__CPROVER_`` primitives excluded)."""
    out = _cbmc(["goto-instrument", "--list-undefined-functions", str(goto)], cwd)
    names = {l.strip() for l in out.splitlines()
             if l.strip() and not l.startswith("Reading GOTO program")}
    return sorted(n for n in names if n not in functions and not n.startswith("__CPROVER"))


def symbol_files(goto, cwd):
    """Every source file a symbol of the goto binary was declared in."""
    # The plain-text table is a few MB where the JSON one is far larger.
    out = _cbmc(["cbmc", str(goto), "--show-symbol-table"], cwd)
    return {os.path.realpath(os.path.join(cwd, m.group(1)))
            for m in re.finditer(r"^Location\.+: file (.+?) line \d+", out, re.M)}


def check_stub_files(cfg, files):
    """(stub labels, problems) for model/contract files compiled into the harness."""
    listed, problems = [], []
    for kind, base in (("models", run.MODELS), ("contracts", run.CONTRACTS)):
        names = list(cfg.get(kind, []))
        listed += [f"{kind}/{n}.c" for n in names]
        base = os.path.realpath(base) + os.sep
        for f in sorted(files):
            if not f.startswith(base):
                continue
            p = pathlib.Path(f)
            if p.stem not in names or p.suffix not in (".c", ".h"):
                problems.append(f"unlisted {kind[:-1]} {kind}/{p.relative_to(base)} is compiled in; "
                                f"list it in proof.toml {kind}")
    return listed, problems


def reachability(goto, cfg, cwd, hdir, files, candidates):
    """(unreachable assertion problems, reached body-less functions).

    Body-less candidates get a generated ``assert(false)`` body, then ``cbmc
    --cover assertion`` reports which assertions are reachable: the harness's
    own and those generated bodies (a reached body-less function).
    """
    with tempfile.TemporaryDirectory() as tmp:
        target = goto
        if candidates:
            target = os.path.join(tmp, "bodies.goto")
            regex = "^(" + "|".join(map(re.escape, candidates)) + ")$"
            _cbmc(["goto-instrument", "--generate-function-body", regex,
                   "--generate-function-body-options", "assert-false", str(goto), target], cwd)
        out = _cbmc([*run.cbmc_base(target, cfg), "--cover", "assertion", "--json-ui"], cwd)
    goals = next((m["goals"] for m in json.loads(out) if "goals" in m), None)
    if goals is None:
        raise AuditError("cbmc --cover assertion printed no goals")
    goal_lines, unreachable, reached = set(), [], set()
    for g in goals:
        loc = g.get("sourceLocation", {})
        sat = g.get("status") == "satisfied"
        if g.get("description") == UNDEFINED_BODY and loc.get("function") in candidates:
            if sat:
                reached.add(loc["function"])
            continue
        path = os.path.realpath(os.path.join(loc.get("workingDirectory", cwd), loc.get("file", "")))
        if not _in_dir(path, hdir) or "line" not in loc:
            continue
        # Every assertion located in harness code must be reachable, however
        # it was written (directly, through a macro, in a helper or header).
        rel = os.path.relpath(path, os.path.realpath(hdir))
        goal_lines.add((path, int(loc["line"])))
        if not sat:
            key = (rel, int(loc["line"]), g.get("description", ""))
            if key not in unreachable:
                unreachable.append(key)
    problems = [f"{rel}:{line}: assertion is unreachable (vacuous): {desc}"
                for rel, line, desc in sorted(unreachable)]
    problems += [f"{os.path.relpath(f, os.path.realpath(hdir))}:{line}: "
                 "__CPROVER_assert has no assertion goal"
                 for f in files for line in assert_lines(f) if (f, line) not in goal_lines]
    return problems, sorted(reached)


# -- rule 4: mutation -----------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class Mutant:
    line: int
    original: str
    mutated: str
    text: str                       # the whole mutated file

    @property
    def diff(self):
        return f"{self.original.strip()} ==> {self.mutated.strip()}"


def mutant_from(original_lines, text):
    """The single-line change ``text`` makes to the source, or None.

    universalmutator's regexp mutants replace one line with one line.
    """
    new = text.splitlines(keepends=True)
    if len(new) != len(original_lines):
        return None
    changed = [i for i, (a, b) in enumerate(zip(original_lines, new)) if a != b]
    if len(changed) != 1:
        return None
    i = changed[0]
    return Mutant(i + 1, original_lines[i].rstrip("\n"), new[i].rstrip("\n"), text)


def generate_mutants(source, lines):
    """universalmutator mutants of ``source`` restricted to ``lines``, deduplicated."""
    source = pathlib.Path(source).resolve()
    original = source.read_text(errors="replace")
    original_lines = original.splitlines(keepends=True)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        (tmp / "lines.txt").write_text("\n".join(map(str, lines)) + "\n")
        (tmp / "m").mkdir()
        # cwd=tmp: mutate writes its scratch copy (.tmp_mutant.*) to the cwd.
        r = subprocess.run(["mutate", str(source), "--noCheck", "--lines", "lines.txt",
                            "--mutantDir", "m"], cwd=tmp, capture_output=True,
                           text=True, errors="replace", timeout=600)
        if r.returncode != 0:
            raise AuditError(f"mutate failed: exit {r.returncode}")
        seen, out = set(), []
        for p in sorted((tmp / "m").iterdir()):
            text = p.read_text(errors="replace")
            if text in seen:
                continue
            seen.add(text)
            m = mutant_from(original_lines, text)
            if m is not None and m.line in lines:
                out.append(m)
    return out


def order_mutants(mutants, seed=SEED):
    """A reproducible order: sort by content, then shuffle with a fixed seed."""
    ordered = sorted(mutants, key=lambda m: (m.line, m.mutated))
    random.Random(seed).shuffle(ordered)
    return ordered


def _write_tree(base, source, text):
    """base/<repo path of source> holding ``text``, siblings symlinked so that the
    copy's own relative #includes resolve as they do for the original."""
    rel = source.relative_to(ROOT)
    dst = base / rel
    dst.parent.mkdir(parents=True, exist_ok=True)
    for sib in source.parent.iterdir():
        if sib.name != source.name:
            os.symlink(sib, dst.parent / sib.name)
    dst.write_text(text)
    return dst


def _include_dirs(base, source, harness_c):
    """-I directories that make the harness's #include pick up base/<rel source>."""
    rel = source.relative_to(ROOT)
    code = pathlib.Path(harness_c).read_text(errors="replace")
    if f'#include "{rel.as_posix()}"' in code:
        return [str(base)]
    return [str(base), str(base / rel.parent)]


def _compiles(hdir, cfg, source, text, tmp, tag, compile_db):
    base = pathlib.Path(tmp) / tag
    _write_tree(base, source, text)
    dirs = _include_dirs(base, source, hdir / "harness.c")
    try:
        run.compile_harness(hdir, cfg, base / "probe.goto", compile_db, dirs)
    except run.HarnessError:
        return None
    return dirs


def select_mutants(hdir, cfg, source, mutants, limit, tmp, jobs, compile_db=None):
    """[(mutant, include dirs)] for the first ``limit`` mutants that compile, in order.

    Mutants that goto-cc rejects are discarded.  Returns (selected, discarded).
    """
    selected, discarded, i = [], 0, 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
        while i < len(mutants) and len(selected) < limit:
            batch = mutants[i:i + max(jobs, limit - len(selected))]
            futs = [pool.submit(_compiles, hdir, cfg, source, m.text, tmp, f"m{i + k}", compile_db)
                    for k, m in enumerate(batch)]
            for m, f in zip(batch, futs):
                dirs = f.result()
                if dirs is None:
                    discarded += 1
                elif len(selected) < limit:
                    selected.append((m, dirs))
            i += len(batch)
    return selected, discarded


def check_substitution(hdir, cfg, source, tmp, compile_db=None):
    """Fail unless a broken copy of the source in the mutant tree breaks the build,
    i.e. unless the harness really compiles the substituted copy."""
    text = "#error audit substitution probe\n" + source.read_text(errors="replace")
    if _compiles(hdir, cfg, source, text, tmp, "probe", compile_db) is not None:
        raise AuditError("mutant substitution does not reach the harness's #include")


def _equivalents(cfg):
    """{(offset, diff): reason} from proof.toml equivalent_mutants."""
    out = {}
    for e in cfg.get("equivalent_mutants", []):
        if (not isinstance(e, dict) or not isinstance(e.get("offset"), int)
                or isinstance(e.get("offset"), bool) or not e.get("diff") or not str(e.get("reason", "")).strip()):
            raise AuditError("proof.toml: equivalent_mutants entries need 'offset' (int), "
                             "'diff' and 'reason'")
        out[(e["offset"], " ".join(e["diff"].split()))] = e["reason"]
    return out


def _describe(keys):
    return [f"offset {o}: {d}" for o, d in keys]


def classify(result):
    """killed | vacuous | error | pass, for one mutant's run.Result."""
    if result.status == "fail":
        return "vacuous" if result.detail.startswith("vacuous") else "killed"
    return result.status if result.status in ("pass", "error") else "error"


@dataclasses.dataclass
class Gate:
    total: int = 0
    killed: int = 0
    vacuous: int = 0
    error: int = 0
    problems: list = dataclasses.field(default_factory=list)
    demote: bool = False
    unused_equivalents: list = dataclasses.field(default_factory=list)


def mutation_gate(hdir, cfg, goto_fns, baseline_seconds, jobs, fail_fast, compile_db=None):
    """Rule 4.  A mutant is killed only when one of its properties fails.

    A mutant whose proof passes is excused only by an ``equivalent_mutants``
    entry with the same offset (line minus the function's first line) and diff.
    Vacuous, errored and timed-out mutants are never excused.
    """
    gate = Gate()
    equivalent = _equivalents(cfg)
    gate.unused_equivalents = _describe(equivalent)       # until a sample matches them
    source = (ROOT / cfg["source"]).resolve()
    lines = function_lines(goto_fns, cfg["function"], source)
    if not lines:
        gate.problems.append(f"mutation: no lines of {cfg['function']} found in {cfg['source']}")
        gate.demote = True
        return gate
    first = lines[0]
    limit = int(cfg.get("mutants_max", MUTANTS_MAX))
    timeout = max(3 * baseline_seconds, MIN_MUTANT_TIMEOUT)
    mutants = order_mutants(generate_mutants(source, set(lines)))
    with tempfile.TemporaryDirectory() as tmp:
        tmp = pathlib.Path(tmp)
        check_substitution(hdir, cfg, source, tmp, compile_db)
        selected, _ = select_mutants(hdir, cfg, source, mutants, limit, tmp, jobs, compile_db)
        sampled = {(m.line - first, " ".join(m.diff.split())) for m, _ in selected}
        gate.unused_equivalents = _describe(k for k in equivalent if k not in sampled)
        with concurrent.futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            futs = {pool.submit(run.run_harness, hdir, tmp / f"out{n}", compile_db, dirs, timeout): m
                    for n, (m, dirs) in enumerate(selected)}
            for fut in concurrent.futures.as_completed(futs):
                m, r = futs[fut], fut.result()
                gate.total += 1
                kind = classify(r)
                where = f"line {m.line}, offset {m.line - first}"
                if kind == "killed":
                    gate.killed += 1
                    continue
                if kind == "pass":
                    if (m.line - first, " ".join(m.diff.split())) in equivalent:
                        continue
                    gate.problems.append(f"surviving mutant ({where}): {m.diff}")
                elif kind == "vacuous":
                    gate.vacuous += 1
                    gate.problems.append(f"vacuous mutant, not killed ({where}): {m.diff} [{r.detail}]")
                else:
                    gate.error += 1
                    what = "timeout" if "timeout" in r.detail else "mutant errored"
                    gate.problems.append(f"{what}, not killed ({where}): {m.diff} [{r.detail}]")
                gate.demote = True
                if fail_fast:
                    for f in futs:
                        f.cancel()
                    break
    if gate.total == 0:
        gate.problems.append("mutation: no mutant compiled, so the postcondition is untested")
        gate.demote = True
    if gate.demote:
        gate.problems.append(f"mutation: {gate.total - gate.killed} of {gate.total} mutants "
                             "not killed; demoted to T1")
    return gate


# -- the audit -------------------------------------------------------------------

def _current_result(hdir, out, digest, compile_db):
    """The harness's result.json if it was made from these inputs, else a fresh run."""
    path = run._out_dir(hdir, pathlib.Path(out)) / "result.json"
    try:
        res = run.Result(**json.loads(path.read_text()))
        if res.input_hash == digest and res.status in ("pass", "fail"):
            return res
    except (OSError, ValueError, TypeError):
        pass
    return run.run_harness(hdir, out, compile_db)


def audit(hdir, out=OUT, jobs=None, fail_fast=False, compile_db=None):
    """Audit one harness directory and write ``<out>/<harness>/audit.json``."""
    hdir = pathlib.Path(hdir).resolve()
    v = Verdict()
    try:
        _audit(hdir, v, out, jobs or os.cpu_count() or 1, fail_fast, compile_db)
    except (AuditError, run.HarnessError, run.StaleBuild) as exc:
        v.ok = False
        v.problems.append(str(exc))
    except subprocess.TimeoutExpired as exc:
        v.ok = False
        v.problems.append(f"timeout after {exc.timeout}s")
    if not v.ok:
        v.audited_tier = None
    dst = run._out_dir(hdir, pathlib.Path(out))
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "audit.json").write_text(json.dumps(v.to_json(), indent=1) + "\n")
    return v


def _audit(hdir, v, out, jobs, fail_fast, compile_db):
    cfg = run._read_config(hdir)           # rule 1: disable_checks needs disable_reason
    tier = cfg["tier"]
    v.input_hash = run.input_hash(hdir, compile_db=compile_db)
    result = _current_result(hdir, out, v.input_hash, compile_db)
    if result.input_hash != v.input_hash:
        raise AuditError("result.json was not made from the current harness inputs")
    if result.status != "pass":
        raise AuditError(f"proof did not pass ({result.status}): {result.detail}")

    fatal = check_flags(cfg, result)                                    # rule 1
    files = harness_files(hdir, cfg, compile_db)
    fatal += line_directives(files, hdir)                               # rule 2
    fatal += foreign_includes(hdir, cfg, compile_db)                    # rule 3
    v.assumes = [a for f in files for a in assume_texts(f, os.path.relpath(f, hdir))]
    for n in cfg.get("models", []):
        v.assumes += assume_texts(run.MODELS / f"{n}.c", f"models/{n}.c")

    havoc = cfg.get("havoc_callees", [])
    if not isinstance(havoc, list) or not all(isinstance(h, str) for h in havoc):
        raise AuditError("proof.toml: havoc_callees must be a list of strings")
    if havoc and not str(cfg.get("havoc_reason", "")).strip():
        fatal.append("havoc_callees needs a non-empty havoc_reason")
    hstubs = cfg.get("harness_stubs", [])
    if not isinstance(hstubs, list) or not all(isinstance(h, str) for h in hstubs):
        raise AuditError("proof.toml: harness_stubs must be a list of strings")
    if hstubs and not str(cfg.get("harness_stubs_reason", "")).strip():
        fatal.append("harness_stubs needs a non-empty harness_stubs_reason")

    with tempfile.TemporaryDirectory() as tmp:
        goto = pathlib.Path(tmp) / "harness.goto"
        cwd, _ = run.compile_harness(hdir, cfg, goto, compile_db)
        v.stubs, stub_problems = check_stub_files(cfg, symbol_files(goto, cwd))   # rule 3
        fatal += stub_problems
        fns = goto_functions(goto, cwd)
        unreachable, reached = reachability(goto, cfg, cwd, hdir, files, bodyless(goto, cwd, fns))  # rule 2
    fatal += unreachable
    unlisted = [f for f in reached if f not in havoc]
    if unlisted:
        fatal.append("reaches body-less functions (CBMC treats their results as arbitrary): "
                     + ", ".join(unlisted) + "; add a model or list them in havoc_callees")
    v.stubs += [f"havoc:{h}" for h in havoc]
    notes = [f"havoc_callees entry {h!r} is not reached" for h in havoc if h not in reached]
    defined = harness_stubs(fns, hdir, cfg["function"])
    unlisted = [f for f in defined if f not in hstubs]
    if unlisted:
        fatal.append(f"{cfg['function']} can reach functions defined in harness code: "
                     + ", ".join(unlisted) + "; list them in harness_stubs")
    v.stubs += [f"harness:{h}" for h in hstubs]
    notes += [f"harness_stubs entry {h!r} is not reachable from {cfg['function']}"
              for h in hstubs if h not in defined]

    if run.input_hash(hdir, compile_db=compile_db) != v.input_hash:
        fatal.append("harness inputs changed while the audit ran")
    v.problems += fatal + notes
    if fatal:
        v.ok = False
        return
    v.ok = True
    v.audited_tier = tier
    # No mutants are sampled below T2 (or for T3, for now): every entry is unused.
    v.unused_equivalents = _describe(_equivalents(cfg))
    if tier == "T3":                                                    # rule 5
        if "unwind" in cfg:
            v.problems.append("T3: proof.toml must not set unwind")
        v.problems.append("T3: contract mode (goto-instrument --dfcc) is not implemented "
                          "yet; demoted to T1")
        v.audited_tier = "T1"
    elif tier == "T2":                                                  # rule 4
        gate = mutation_gate(hdir, cfg, fns, result.seconds, jobs, fail_fast, compile_db)
        v.mutants_total, v.mutants_killed = gate.total, gate.killed
        v.mutants_vacuous, v.mutants_error = gate.vacuous, gate.error
        v.unused_equivalents = gate.unused_equivalents
        v.problems += gate.problems
        if gate.demote:
            v.audited_tier = "T1"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", help="glob on the harness path relative to harnesses/")
    ap.add_argument("--jobs", type=int, default=None, help="parallel mutant runs (default: cpu count)")
    ap.add_argument("--fail-fast", action="store_true", help="stop mutating at the first survivor")
    args = ap.parse_args(argv)
    dirs = run.discover(args.only)
    if not dirs:
        print("no harnesses matched", file=sys.stderr)
        return 1
    rc = 0
    for d in dirs:
        start = time.monotonic()
        v = audit(d, jobs=args.jobs, fail_fast=args.fail_fast)
        try:
            declared = run._read_config(d)["tier"]
        except run.HarnessError:
            declared = "?"
        print(f"{'ok' if v.ok else 'FAIL':4} {declared} -> {v.audited_tier or '-'}  "
              f"mutants {v.mutants_killed}/{v.mutants_total}  "
              f"{len(v.problems)} problem(s)  {time.monotonic() - start:7.1f}s  "
              f"{d.relative_to(run.HARNESSES)}")
        for p in v.problems:
            print(f"     - {p}")
        if not v.ok:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
