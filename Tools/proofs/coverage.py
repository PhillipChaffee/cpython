"""Merge the line inventory with proof results into the proof-coverage report.

The denominator is ``out/inventory.json``.  Each harness contributes its
``result.json`` combined with its ``audit.json``; only a passing harness whose
audit exists and is ``ok`` counts, at its *audited* tier.  A line's tier is the
highest tier of any counting harness that covers it, and a T3 line is also a
T2 and T1 line.

Outputs in ``out/``: ``proof-coverage.json``, ``proof-coverage-t{1,2,3}.info``
(lcov) and ``unreached.md``.  Exit codes: 0 ok, 1 threshold or ratchet
failure, 2 usage or input error.
"""

import argparse
import dataclasses
import json
import math
import pathlib
import sys
import tomllib

import inventory
import run

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = HERE / "out"
REASONS = HERE / "unreached_reasons.toml"
TIERS = run.TIERS                      # ("T1", "T2", "T3")
RANK = {t: i + 1 for i, t in enumerate(TIERS)}
REASON_CODES = ("no-harness", "unwind-infeasible", "platform-ifdef", "os-behaviour",
                "inline-asm", "solver-timeout", "needs-contract")
SKIP_DIRS = {"_fixtures"}


@dataclasses.dataclass
class HarnessRecord:
    """One harness: its result.json combined with its audit.json."""
    harness: str                    # path of the harness directory below out/
    status: str                     # "pass" | "fail" | "error"
    declared_tier: str
    audited_tier: str = None        # from audit.json; None when not audited
    audited: bool = False           # an audit.json exists
    audit_ok: bool = True           # audit.json "ok"
    covered: dict = dataclasses.field(default_factory=dict)
    target: tuple = None            # (source, function) from proof.toml
    invalid: str = ""               # why it cannot count: "stale", "harness deleted"


def _zero():
    return {"total": 0, "t1": 0, "t2": 0, "t3": 0}


@dataclasses.dataclass
class Report:
    total_lines: int = 0
    t1: int = 0
    t2: int = 0
    t3: int = 0
    by_dir: dict = dataclasses.field(default_factory=dict)
    by_file: dict = dataclasses.field(default_factory=dict)
    by_function: dict = dataclasses.field(default_factory=dict)   # path -> name -> counts
    functions_total: int = 0
    functions_t1: int = 0           # at least one line covered at the tier
    functions_t2: int = 0
    functions_t3: int = 0
    functions_full_t1: int = 0      # every line covered at the tier
    functions_full_t2: int = 0
    functions_full_t3: int = 0
    harnesses_counted: int = 0
    skipped: dict = dataclasses.field(default_factory=dict)       # harness -> reason
    not_in_denominator: dict = dataclasses.field(default_factory=dict)
    provisional: bool = False
    line_tier: dict = dataclasses.field(default_factory=dict)     # path -> {line: 0..3}

    def to_json(self):
        d = dataclasses.asdict(self)
        del d["line_tier"]
        d["upper_bound"] = bool(self.not_in_denominator)
        for r in (1, 2, 3):
            d[f"pct_t{r}"] = pct_floor(getattr(self, f"t{r}"), self.total_lines)
        return d


def pct_floor(n, total):
    """Percentage rounded DOWN to two decimals (99.996 is 99.99, never 100.00)."""
    return (n * 10000 // total) / 100 if total else 0.0


def _tier_of(rec, include_unaudited):
    """The tier this record counts at, or None, plus the reason when None."""
    if rec.invalid:
        return None, rec.invalid
    if rec.status != "pass":
        return None, f"status {rec.status}"
    if rec.audited:
        if not rec.audit_ok:
            return None, "audit failed"
        if rec.audited_tier not in RANK:
            return None, "audit gave no tier"
        return rec.audited_tier, ""
    if include_unaudited:
        return "T1", ""
    return None, "not audited"


def merge(inv, records, include_unaudited=False):
    if hasattr(inv, "to_json"):
        inv = inv.to_json()
    files = inv.get("files", {})
    rep = Report(not_in_denominator=dict(inv.get("errors", {})),
                 provisional=include_unaudited)
    line_tier = {p: {l: 0 for fn in f["functions"].values() for l in fn["lines"]}
                 for p, f in files.items()}
    for rec in records:
        tier, why = _tier_of(rec, include_unaudited)
        if tier is None:
            rep.skipped[rec.harness] = why
            continue
        rep.harnesses_counted += 1
        # T2/T3 credit is for the target function only: a postcondition on f
        # says nothing about the correctness of its callees.
        tsrc, tfunc = rec.target or (None, None)
        tfn = files.get(tsrc, {}).get("functions", {}).get(tfunc)
        target_lines = set(tfn["lines"]) if tfn else set()
        for path, lines in rec.covered.items():
            lt = line_tier.get(path)
            if lt is None:
                continue
            for line in lines:
                rank = RANK[tier]
                if rank > 1 and not (path == tsrc and line in target_lines):
                    rank = 1
                if line in lt and lt[line] < rank:
                    lt[line] = rank
    rep.line_tier = line_tier

    for path, f in sorted(files.items()):
        lt = line_tier[path]
        top = path.split("/")[0] if "/" in path else "."
        fcount, dcount = _zero(), rep.by_dir.setdefault(top, _zero())
        fcount["total"] = len(lt)
        for rank in (1, 2, 3):
            fcount[f"t{rank}"] = sum(1 for v in lt.values() if v >= rank)
        for k in fcount:
            dcount[k] += fcount[k]
        rep.by_file[path] = fcount
        funcs = rep.by_function.setdefault(path, {})
        for name, fn in sorted(f["functions"].items()):
            c = _zero()
            c["total"] = len(fn["lines"])
            c["static"] = fn.get("static", False)
            for rank in (1, 2, 3):
                n = sum(1 for l in fn["lines"] if lt[l] >= rank)
                c[f"t{rank}"] = n
                if n:
                    setattr(rep, f"functions_t{rank}", getattr(rep, f"functions_t{rank}") + 1)
                if n and n == c["total"]:
                    setattr(rep, f"functions_full_t{rank}", getattr(rep, f"functions_full_t{rank}") + 1)
            funcs[name] = c
            rep.functions_total += 1
    rep.total_lines = sum(c["total"] for c in rep.by_file.values())
    for rank in (1, 2, 3):
        setattr(rep, f"t{rank}", sum(c[f"t{rank}"] for c in rep.by_file.values()))
    return rep


def _target(hdir):
    try:
        with open(hdir / "proof.toml", "rb") as fh:
            cfg = tomllib.load(fh)
        return (cfg["source"], cfg["function"])
    except (OSError, tomllib.TOMLDecodeError, KeyError):
        return None


def load_records(out, harness_root=run.HARNESSES, hash_fn=None):
    """Read every ``<harness>/result.json`` (+ ``audit.json``) below ``out``.

    A record is marked invalid ("harness deleted") when its directory no longer
    exists below ``harness_root``, or ("stale") when the input hash of the
    result or of the audit differs from ``hash_fn`` of the harness now.
    """
    out = pathlib.Path(out)
    hash_fn = hash_fn or run.input_hash
    records = []
    for rpath in sorted(out.rglob("result.json")):
        rel = rpath.parent.relative_to(out)
        if rel.parts and rel.parts[0] in SKIP_DIRS:
            continue
        hdir = pathlib.Path(harness_root) / rel
        try:
            raw = json.loads(rpath.read_text())
            res = run.Result(**raw)
        except (ValueError, TypeError) as exc:
            records.append(HarnessRecord(str(rel), "error", "", invalid="unreadable result.json"))
            print(f"warning: unreadable {rpath}: {exc}", file=sys.stderr)
            continue
        rec = HarnessRecord(str(rel), res.status, res.tier, covered=res.covered)
        if not (hdir / "proof.toml").exists():
            rec.invalid = "harness deleted"
            records.append(rec)
            continue
        rec.target = _target(hdir)
        apath = rpath.parent / "audit.json"
        audit_hash = None
        if apath.exists():
            rec.audited = True
            try:
                audit = json.loads(apath.read_text())
            except ValueError:
                audit = None
            if isinstance(audit, dict) and audit.get("audited_tier") in RANK:
                rec.audit_ok = audit.get("ok") is True
                rec.audited_tier = audit["audited_tier"]
                audit_hash = audit.get("input_hash")
            else:
                rec.audit_ok = False
        try:
            now = hash_fn(hdir)
        except Exception:                # HarnessError and friends: cannot verify
            now = None
        if now is None or res.input_hash != now or (rec.audited and audit_hash != now):
            rec.invalid = "stale"
        records.append(rec)
    return records


def build_report(out=OUT, include_unaudited=False, inventory_path=None,
                 harness_root=run.HARNESSES):
    out = pathlib.Path(out)
    ipath = pathlib.Path(inventory_path) if inventory_path else out / "inventory.json"
    inv = json.loads(ipath.read_text())
    return merge(inv, load_records(out, harness_root), include_unaudited)


def load_reasons(path=REASONS):
    path = pathlib.Path(path)
    if not path.exists():
        return {}
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    for src, table in data.items():
        if not isinstance(table, dict):
            raise ValueError(f"{path}: [{src!r}] must be a table of function -> reason")
        _check_codes(table, str(path), src)
    return data


def _check_codes(table, where, src):
    for func, code in table.items():
        if code not in REASON_CODES:
            raise ValueError(f"{where}: {src}:{func}: unknown reason {code!r}; "
                             f"allowed: {', '.join(REASON_CODES)}")


def _reason(reasons, path, func):
    table = reasons.get(path)
    if isinstance(table, dict):
        if func in table:
            return table[func]
        if "*" in table:
            return table["*"]
    flat = reasons.get(func)
    return flat if isinstance(flat, str) else "no-harness"


def render_unreached(rep, reasons=None):
    reasons = reasons or {}
    for key, val in reasons.items():
        if isinstance(val, dict):
            _check_codes(val, "reasons", key)
        elif val not in REASON_CODES:
            raise ValueError(f"reasons: {key}: unknown reason {val!r}; "
                             f"allowed: {', '.join(REASON_CODES)}")
    out = ["PROVISIONAL — unaudited figures", ""] if rep.provisional else []
    out += ["# Unreached code", ""]
    if rep.not_in_denominator:
        out += [f"## Files not in the denominator ({len(rep.not_in_denominator)})", "",
                "The inventory could not analyse these files: size unknown, uncovered.", ""]
        out += [f"- {p} — size unknown, uncovered: {why}"
                for p, why in sorted(rep.not_in_denominator.items())]
        out.append("")
    out += ["## Functions with no covered line", ""]
    for path, funcs in rep.by_function.items():
        dead = [n for n, c in funcs.items() if c["t1"] == 0]
        if not dead:
            continue
        out += [f"### {path} ({len(dead)})", ""]
        out += [f"{path}:{n} — {_reason(reasons, path, n)}" for n in dead]
        out.append("")
    return "\n".join(out)


def render_lcov(rep, tier, root=ROOT):
    rank = RANK[tier]
    out = []
    for path, lt in rep.line_tier.items():
        out += ["TN:provisional_unaudited" if rep.provisional else "TN:", f"SF:{pathlib.Path(root) / path}"]
        out += [f"DA:{l},{int(lt[l] >= rank)}" for l in sorted(lt)]
        out += [f"LF:{len(lt)}", f"LH:{sum(1 for v in lt.values() if v >= rank)}",
                "end_of_record"]
    return "\n".join(out) + "\n"


def _pct(n, total):
    if not total:
        return "n/a"
    v = n * 10000 // total
    return f"{v // 100}.{v % 100:02d}%"


def render_summary(rep):
    lines = []
    if rep.provisional:
        lines.append("PROVISIONAL: unaudited harnesses are counted at T1 only; "
                     "these figures are unaudited and must not be relied on.")
    lines.append(f"total lines: {rep.total_lines}")
    for r in (1, 2, 3):
        lines.append(f"T{r} lines covered: {getattr(rep, f't{r}')} "
                     f"({_pct(getattr(rep, f't{r}'), rep.total_lines)})")
    if rep.not_in_denominator:
        lines.append(f"percentages are upper bounds: {len(rep.not_in_denominator)} "
                     "file(s) not in denominator")
    lines.append(f"functions total: {rep.functions_total}")
    for r in (1, 2, 3):
        lines.append(f"T{r} functions: {getattr(rep, f'functions_t{r}')} fully or partly covered, "
                     f"{getattr(rep, f'functions_full_t{r}')} fully covered")
    lines += ["", f"{'directory':<20}{'lines':>9}{'T1':>9}{'T1 %':>9}{'T2 %':>9}{'T3 %':>9}"]
    for d, c in sorted(rep.by_dir.items()):
        lines.append(f"{d:<20}{c['total']:>9}{c['t1']:>9}{_pct(c['t1'], c['total']):>9}"
                     f"{_pct(c['t2'], c['total']):>9}{_pct(c['t3'], c['total']):>9}")
    lines.append("")
    lines.append(f"harnesses counted: {rep.harnesses_counted}, skipped: {len(rep.skipped)}")
    for h, why in sorted(rep.skipped.items()):
        lines.append(f"  skipped {h}: {why}")
    lines.append(f"not in denominator: {len(rep.not_in_denominator)} file(s) "
                 "(size unknown, uncovered; see unreached.md)")
    return "\n".join(lines) + "\n"


def render_file(rep, path):
    funcs = rep.by_function[path]

    def tier(c):
        return next((f"T{r}" for r in (3, 2, 1) if c[f"t{r}"]), "-")

    rows = sorted(funcs.items(), key=lambda kv: (kv[1]["t1"] / kv[1]["total"]
                                                 if kv[1]["total"] else 1, kv[0]))
    out = [f"{path}: {rep.by_file[path]['t1']}/{rep.by_file[path]['total']} lines at T1"]
    out += [f"{n:<40} {c['t1']}/{c['total']} lines  tier {tier(c)}" for n, c in rows]
    return "\n".join(out) + "\n"


def write_outputs(rep, out, reasons):
    out = pathlib.Path(out)
    (out / "proof-coverage.json").write_text(json.dumps(rep.to_json(), indent=1) + "\n")
    for t in TIERS:
        (out / f"proof-coverage-{t.lower()}.info").write_text(render_lcov(rep, t))
    (out / "unreached.md").write_text(render_unreached(rep, reasons))


def main(argv=None, report=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=pathlib.Path, default=OUT, help="directory holding the artifacts")
    ap.add_argument("--inventory", type=pathlib.Path, help="default: <out>/inventory.json")
    ap.add_argument("--harnesses", type=pathlib.Path, default=run.HARNESSES,
                    help="directory holding the harness sources (default: Tools/proofs/harnesses)")
    ap.add_argument("--reasons", type=pathlib.Path, default=REASONS)
    ap.add_argument("--fail-under-t1", type=float, metavar="PCT",
                    help="exit 1 if T1 line coverage is below PCT percent")
    ap.add_argument("--baseline", type=pathlib.Path,
                    help='JSON {"t1": n, "t2": n, "t3": n}; exit 1 if any tier covers fewer lines')
    ap.add_argument("--include-unaudited", action="store_true",
                    help="PROVISIONAL: count passing, unaudited harnesses at T1 only")
    ap.add_argument("--file", metavar="PATH", help="print per-function coverage of one file")
    args = ap.parse_args(argv)
    gated = args.fail_under_t1 is not None or args.baseline is not None
    if args.include_unaudited and gated:
        ap.error("--include-unaudited cannot be combined with --fail-under-t1 or --baseline")
    if args.file and gated:
        ap.error("--file cannot be combined with --fail-under-t1 or --baseline")
    if args.fail_under_t1 is not None and not (
            math.isfinite(args.fail_under_t1) and 0 <= args.fail_under_t1 <= 100):
        ap.error("--fail-under-t1 must be a number from 0 to 100")

    try:
        baseline = None
        if args.baseline:
            baseline = json.loads(args.baseline.read_text())
            if not isinstance(baseline, dict) or not all(
                    type(baseline.get(k)) is int and baseline[k] >= 0 for k in ("t1", "t2", "t3")):
                raise ValueError(f"{args.baseline}: must be an object with "
                                 "non-negative integer t1, t2, t3")
        reasons = load_reasons(args.reasons)
        rep = report
        if rep is None:
            rep = build_report(args.out, args.include_unaudited, args.inventory, args.harnesses)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.file:
        if args.file not in rep.by_function:
            print(f"error: {args.file} is not in the inventory", file=sys.stderr)
            return 2
        print(render_file(rep, args.file), end="")
        return 0

    if report is None:
        write_outputs(rep, args.out, reasons)
    print(render_summary(rep), end="")

    rc = 0
    if args.fail_under_t1 is not None:
        if not rep.total_lines:
            print("error: empty denominator: no inventory lines, cannot check --fail-under-t1",
                  file=sys.stderr)
            return 2
        if 100 * rep.t1 < args.fail_under_t1 * rep.total_lines:
            print(f"FAIL: T1 coverage {_pct(rep.t1, rep.total_lines)} is below "
                  f"{args.fail_under_t1}%", file=sys.stderr)
            rc = 1
    if baseline is not None:
        for t in ("t1", "t2", "t3"):
            if getattr(rep, t) < baseline[t]:
                print(f"FAIL: {t.upper()} covers {getattr(rep, t)} lines, baseline {baseline[t]}",
                      file=sys.stderr)
                rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
