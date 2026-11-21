"""Inventory of executable C lines and functions: the proof-coverage denominator.

For each translation unit in the compile database, compile it with goto-cc
(using the entry's -I/-D/-U/-std/-include flags), then ask CBMC for
``--cover location --show-properties``.  Every goal carries ``basicBlockLines``
for its basic block, so the union over all goals is the set of executable
lines of every function in the unit (reachable from the entry or not).
"""

import argparse
import concurrent.futures
import fnmatch
import functools
import hashlib
import json
import os
import pathlib
import shlex
import subprocess
import sys
import tempfile
import tomllib
from dataclasses import dataclass, field

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = pathlib.Path(__file__).resolve().parent / "out"
ENTRY = "__inventory_entry"
ENTRY_SRC = f"void {ENTRY}(void) {{}}\n"
TIMEOUT = 600

# flag -> takes a separate value when given alone
_KEEP_PREFIX = ("-I", "-D", "-U", "-std=")
_KEEP_WITH_VALUE = ("-I", "-D", "-U", "-include", "-isystem", "-iquote", "-idirafter")


@dataclass(frozen=True)
class Scope:
    exclude: tuple

    def excludes(self, rel_path):
        return any(fnmatch.fnmatch(rel_path, pat) for pat in self.exclude)


def load_scope(path):
    if isinstance(path, Scope):
        return path
    with open(path, "rb") as fh:
        return Scope(tuple(tomllib.load(fh).get("exclude", [])))


@dataclass
class Function:
    lines: list
    static: bool


@dataclass
class FileInventory:
    functions: dict = field(default_factory=dict)


@dataclass
class Inventory:
    files: dict = field(default_factory=dict)
    excluded: list = field(default_factory=list)
    errors: dict = field(default_factory=dict)

    def totals(self):
        funcs = sum(len(f.functions) for f in self.files.values())
        lines = sum(len(fn.lines) for f in self.files.values() for fn in f.functions.values())
        return {"files": len(self.files), "functions": funcs, "lines": lines,
                "errors": len(self.errors)}

    def to_json(self):
        return {
            "files": {
                p: {"functions": {n: {"lines": fn.lines, "static": fn.static}
                                  for n, fn in sorted(f.functions.items())}}
                for p, f in sorted(self.files.items())
            },
            "excluded": sorted(self.excluded),
            "errors": dict(sorted(self.errors.items())),
        }

    def merge(self, raw):
        """Merge one unit's {path: {func: {"lines": [...], "static": bool}}}."""
        for path, funcs in raw.items():
            dst = self.files.setdefault(path, FileInventory()).functions
            for name, info in funcs.items():
                cur = dst.get(name)
                if cur is None:
                    dst[name] = Function(sorted(info["lines"]), info["static"])
                else:
                    cur.lines = sorted(set(cur.lines) | set(info["lines"]))
                    cur.static = cur.static or info["static"]


def parse_lines(text):
    out = set()
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        lo, _, hi = part.partition("-")
        out.update(range(int(lo), int(hi or lo) + 1))
    return out


def entry_flags(entry):
    """The goto-cc-safe subset of a compile-database entry's flags."""
    args = entry.get("arguments") or shlex.split(entry["command"])
    keep, i = [], 1
    while i < len(args):
        a = args[i]
        i += 1
        if a == "-o":
            i += 1
        elif a in _KEEP_WITH_VALUE:
            if i < len(args):
                keep += [a, args[i]]
                i += 1
        elif a.startswith(_KEEP_PREFIX):
            keep.append(a)
    return keep


# Environment variables that add include or program search directories to
# gcc (and so to goto-cc, which preprocesses with gcc), or make it write files.
COMPILER_ENV = ("CPATH", "C_INCLUDE_PATH", "CPLUS_INCLUDE_PATH", "OBJC_INCLUDE_PATH",
                "GCC_EXEC_PREFIX", "COMPILER_PATH", "LIBRARY_PATH",
                "DEPENDENCIES_OUTPUT", "SUNPRO_DEPENDENCIES")


def tool_env():
    """The environment for every gcc, goto-cc and cbmc subprocess: os.environ
    without COMPILER_ENV, so the caller's shell cannot change what is compiled."""
    return {k: v for k, v in os.environ.items() if k not in COMPILER_ENV}


def _run(cmd, cwd):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          errors="replace", timeout=TIMEOUT, env=tool_env())


def goal_lines(goal, cwd):
    """{absolute path: {func: set(lines)}} of one cover goal's basicBlockLines.

    CBMC reports paths relative to the directory goto-cc ran in (``cwd``).
    """
    out = {}
    for path, funcs in (goal.get("basicBlockLines") or {}).items():
        if not os.path.isabs(path):
            path = os.path.normpath(os.path.join(cwd, path))
        dst = out.setdefault(path, {})
        for func, text in funcs.items():
            dst.setdefault(func, set()).update(parse_lines(text))
    return out


def analyse_unit(source, flags, cwd):
    """Return {abs_path: {func: {"lines": [...], "static": bool}}} for one unit.

    Raises RuntimeError with a short reason on failure.
    """
    with tempfile.TemporaryDirectory() as tmp:
        entry_c = os.path.join(tmp, "entry.c")
        goto = os.path.join(tmp, "unit.goto")
        with open(entry_c, "w") as fh:
            fh.write(ENTRY_SRC)
        r = _run(["goto-cc", *flags, str(source), entry_c, "-o", goto], cwd)
        if r.returncode != 0:
            err = [l for l in r.stderr.splitlines() if "error" in l.lower()] or r.stderr.splitlines()
            raise RuntimeError("goto-cc failed: " + (err[0].strip() if err else f"exit {r.returncode}")[:200])
        # --show-properties stops after instrumentation and lists every coverage
        # goal (basic block) of every function, reachable from the entry or not,
        # without running the solver (which takes ~40s for a core file).
        r = _run(["cbmc", goto, "--function", ENTRY, "--cover", "location",
                  "--show-properties", "--json-ui"], cwd)
        if r.returncode != 0:
            raise RuntimeError(f"cbmc cover failed: exit {r.returncode}")
        goals = next((e["properties"] for e in json.loads(r.stdout) if "properties" in e), None)
        if goals is None:
            raise RuntimeError("cbmc output has no properties")
        r = _run(["cbmc", goto, "--show-symbol-table", "--json-ui"], cwd)
        if r.returncode != 0:
            raise RuntimeError(f"cbmc symbol table failed: exit {r.returncode}")
        table = next(e["symbolTable"] for e in json.loads(r.stdout) if "symbolTable" in e)

    static_fns = {n for n, s in table.items()
                  if s.get("isFileLocal") and s.get("type", {}).get("id") == "code"}
    raw = {}
    for goal in goals:
        for path, funcs in goal_lines(goal, cwd).items():
            for func, lines in funcs.items():
                if func == ENTRY:
                    continue
                info = raw.setdefault(path, {}).setdefault(
                    func, {"lines": set(), "static": func in static_fns})
                info["lines"] |= lines
    return {p: {f: {"lines": sorted(i["lines"]), "static": i["static"]} for f, i in fs.items()}
            for p, fs in raw.items()}


def relativise(raw, root):
    """Repo-relative paths; drop system headers and the build directory."""
    out = {}
    root = os.path.realpath(root)
    for path, funcs in raw.items():
        rel = os.path.relpath(os.path.realpath(path), root)
        if rel == ".." or rel.startswith(".." + os.sep) or rel == "build" or rel.startswith("build" + os.sep):
            continue
        out[rel.replace(os.sep, "/")] = funcs
    return out


@functools.lru_cache(maxsize=None)
def cbmc_version():
    r = subprocess.run(["cbmc", "--version"], capture_output=True, text=True, errors="replace",
                       env=tool_env())
    return r.stdout.strip()


def _cache_path(cache_dir, source, flags):
    st = os.stat(source)
    key = json.dumps([str(source), st.st_mtime_ns, st.st_size, flags, cbmc_version()])
    return pathlib.Path(cache_dir) / (hashlib.sha256(key.encode()).hexdigest() + ".json")


def _process(job, root, cache_dir):
    """Analyse one unit; never raises. Only successful results are cached."""
    source, flags, cwd = job
    try:
        cache = _cache_path(cache_dir, source, flags) if cache_dir else None
        if cache is not None and cache.exists():
            try:
                return json.loads(cache.read_text())
            except ValueError:
                pass
        result = {"raw": relativise(analyse_unit(source, flags, cwd), root)}
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(result))
        return result
    except subprocess.TimeoutExpired:
        return {"error": f"timeout after {TIMEOUT}s"}
    except Exception as exc:  # one bad file must not abort the run
        return {"error": f"{type(exc).__name__}: {exc}"[:200]}


def _run_jobs(jobs, root, scope, cache_dir, workers):
    inv = Inventory()
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers or os.cpu_count()) as pool:
        futures = {pool.submit(_process, job, root, cache_dir): job for job in jobs}
        for fut in concurrent.futures.as_completed(futures):
            source = futures[fut][0]
            res = fut.result()
            key = os.path.relpath(source, root).replace(os.sep, "/")
            if "error" in res:
                inv.errors.setdefault(key, res["error"])
                continue
            inv.merge({p: f for p, f in res["raw"].items() if not scope.excludes(p)})
    return inv


def build(compile_db, scope, jobs=None, cache_dir=OUT / "cache", root=ROOT):
    scope = load_scope(scope)
    with open(compile_db) as fh:
        db = json.load(fh)
    todo, excluded, seen = [], set(), set()
    for entry in db:
        cwd = entry["directory"]
        source = os.path.normpath(os.path.join(cwd, entry["file"]))
        flags = entry_flags(entry)
        if (source, tuple(flags), cwd) in seen:
            continue
        seen.add((source, tuple(flags), cwd))
        rel = os.path.relpath(source, root).replace(os.sep, "/")
        if scope.excludes(rel):
            excluded.add(rel)
        else:
            todo.append((source, flags, cwd))
    inv = _run_jobs(todo, root, scope, cache_dir, jobs)
    inv.excluded = sorted(excluded)
    return inv


def build_from_sources(paths, scope, jobs=None):
    """Inventory standalone files (no extra flags); paths are relative to their common parent."""
    scope = load_scope(scope)
    paths = [pathlib.Path(p).resolve() for p in paths]
    root = pathlib.Path(os.path.commonpath([p.parent for p in paths]))
    return _run_jobs([(str(p), [], str(root)) for p in paths], root, scope, None, jobs)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=pathlib.Path, default=OUT / "inventory.json")
    ap.add_argument("--db", type=pathlib.Path, default=ROOT / "build/proofs/compile_commands.json")
    ap.add_argument("--scope", type=pathlib.Path, default=pathlib.Path(__file__).resolve().parent / "scope.toml")
    ap.add_argument("--jobs", type=int, default=None, help="parallel workers (default: cpu count)")
    ap.add_argument("--no-cache", action="store_true",
                    help="ignore and do not write the cache (needed after header edits)")
    ap.add_argument("--allow-errors", action="store_true",
                    help="exit 0 even if some files could not be analysed")
    args = ap.parse_args(argv)
    inv = build(args.db, args.scope, jobs=args.jobs,
                cache_dir=None if args.no_cache else OUT / "cache")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(inv.to_json(), indent=1) + "\n")
    t = inv.totals()
    print(json.dumps(t))
    for path, why in sorted(inv.errors.items()):
        print(f"error: {path}: {why}", file=sys.stderr)
    if inv.errors:
        print(f"WARNING: {t['errors']} file(s) are NOT in these totals "
              "(size unknown); see \"errors\" in the JSON.", file=sys.stderr)
        return 0 if args.allow_errors else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
