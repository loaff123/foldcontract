#!/usr/bin/env python3
"""Independent developer checks; no prototype import or production solver helpers.

Run from the repository root with PYTHONPATH=src. Frozen inputs and old results
are retained, not regenerated. SciPy is imported only with --fresh-milp.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
from itertools import product
import json
from pathlib import Path
import platform
import random
import sys
import time

COHORT_SHA256 = "766577906c518fe1fdd28d99603dbb5227c4235fcde267f9fc15b58c914fc4cf"
ORACLE_SHA256 = "32c4668c4bc85fbb259cd1b0ed55c35da0027b99a451b6caa7d9e001117ecad5"
RESEARCH_SHA256 = "0ec14bb5d68a7b8ce3b1f9f97f9874e32e205b754628d42ae5bdec071b40229e"
ROOT = Path(__file__).resolve().parents[1]


def assignment_indices(raw, assignment):
    """Reject malformed mappings before interpreting values in source order."""
    if isinstance(assignment, dict):
        mapping = assignment
    elif isinstance(assignment, list):
        mapping = {}
        for entry in assignment:
            if not isinstance(entry, dict) or set(entry) != {"group", "partition"}:
                return None
            if entry["group"] in mapping:
                return None
            mapping[entry["group"]] = entry["partition"]
    else:
        return None
    if set(mapping) != set(raw["groups"]):
        return None
    if any(p not in raw["partitions"] for p in mapping.values()):
        return None
    return tuple(raw["partitions"].index(mapping[g]) for g in raw["groups"])


def direct_valid(raw, assignment):
    """Exact recount of a complete integer-index assignment against raw dicts.

    Uses only input class rows and requirements. Does not normalize requirements,
    combine repeated bounds, prune partial assignments, or import FoldContract.
    """
    n, k, c = len(raw["groups"]), len(raw["partitions"]), len(raw["classes"])
    if assignment is None or len(assignment) != n:
        return False
    if any(type(p) is not int or not 0 <= p < k for p in assignment):
        return False
    class_sums = [[0] * c for _ in range(k)]
    groups = [0] * k
    for g, p in enumerate(assignment):
        groups[p] += 1
        for j, value in enumerate(raw["counts"][g]):
            class_sums[p][j] += value
    for bound in raw["requirements"]:
        p = raw["partitions"].index(bound["partition"])
        if bound["measure"] == "groups":
            observed = groups[p]
            total = n
        elif bound["measure"] == "records":
            observed = sum(class_sums[p])
            total = sum(map(sum, raw["counts"]))
        else:
            j = raw["classes"].index(bound["class"])
            observed = class_sums[p][j]
            total = sum(row[j] for row in raw["counts"])
        if not bound.get("lower", 0) <= observed <= bound.get("upper", total):
            return False
    return True


def cartesian_oracle(raw):
    """Visit full Cartesian assignments, stopping only upon a checked witness.

    No branch pruning, symmetry reduction, model objects, solver internals,
    normalized bounds or cached partial states are used. Exhaustion is exact.
    """
    for candidate in product(range(len(raw["partitions"])), repeat=len(raw["groups"])):
        if direct_valid(raw, candidate):
            return {"status": "FEASIBLE", "assignment": list(candidate)}
    return {"status": "INFEASIBLE", "assignment": None}


def translate_frozen(row):
    """Translate each old matrix dimension exactly, without adding constraints.

    The old representation optionally appends total records as its last column.
    That column is removed from class counts and represented by a records bound.
    The assertion prevents counting the appended total as another class.
    """
    c, k = row["classes"], row["k"]
    width = len(row["counts"][0])
    if width not in (c, c + 1):
        raise ValueError("unexpected frozen dimension count")
    if any(len(values) != width for values in row["counts"]):
        raise ValueError("ragged frozen count matrix")
    if width == c + 1 and any(values[c] != sum(values[:c]) for values in row["counts"]):
        raise ValueError("frozen appended record total is inconsistent")
    if len(row["lower"]) != k or len(row["upper"]) != k:
        raise ValueError("wrong frozen bound partition count")
    raw = {"version": 1, "groups": [f"g{g:03d}" for g in range(len(row["counts"]))],
           "classes": [f"class{j:02d}" for j in range(c)],
           "partitions": [f"fold{p:02d}" for p in range(k)],
           "counts": [values[:c] for values in row["counts"]], "requirements": []}
    for p in range(k):
        if len(row["lower"][p]) != width or len(row["upper"][p]) != width:
            raise ValueError("wrong frozen bound measure count")
        for j in range(width):
            bound = {"id": f"fold{p:02d}-dimension{j:02d}",
                     "partition": raw["partitions"][p],
                     "measure": "class" if j < c else "records",
                     "lower": row["lower"][p][j], "upper": row["upper"][p][j]}
            if j < c:
                bound["class"] = raw["classes"][j]
            raw["requirements"].append(bound)
    return raw


def random_cases(number=1200, seed=2026100501):
    """Post-protocol adversarial cases; never presented as primary cohort rows."""
    rng = random.Random(seed)
    cases = []
    for index in range(number):
        n, k, c = rng.randint(1, 6), rng.randint(2, 3), rng.randint(1, 4)
        counts = [[rng.choice([0, 0, 0, 1, 2, 3, 8]) for _ in range(c)] for _ in range(n)]
        for values in counts:
            if not any(values):
                values[rng.randrange(c)] = 1
        for j in range(c):
            if not any(values[j] for values in counts):
                counts[rng.randrange(n)][j] = 1
        raw = {"version": 1, "groups": [f"group-{n-g}" for g in range(n)],
               "classes": [f"label-{c-j}" for j in range(c)],
               "partitions": [f"partition-{k-p}" for p in range(k)],
               "counts": counts, "requirements": []}
        planted = [rng.randrange(k) for _ in range(n)]
        for q in range(0 if index % 29 == 0 else rng.randint(1, 14)):
            measure = rng.choice(["class", "records", "groups"])
            p, j = rng.randrange(k), rng.randrange(c)
            weights = ([1] * n if measure == "groups" else
                       [sum(row) for row in counts] if measure == "records" else
                       [row[j] for row in counts])
            observed = sum(w for g, w in enumerate(weights) if planted[g] == p)
            total = sum(weights)
            if index % 3 == 0:
                lo, hi = rng.randint(0, observed), rng.randint(observed, total)
            else:
                lo, hi = rng.randint(0, total + 2), rng.randint(0, total + 2)
                if index % 3 == 1:
                    lo, hi = sorted((lo, hi))
            req = {"id": f"req-{q:02d}", "partition": raw["partitions"][p], "measure": measure}
            if measure == "class":
                req["class"] = raw["classes"][j]
            mode = rng.randrange(3)
            if mode != 1:
                req["lower"] = lo
            if mode != 0:
                req["upper"] = hi
            raw["requirements"].append(req)
            if q % 4 == 0:
                duplicate = dict(req, id=f"duplicate-{q:02d}")
                raw["requirements"].append(duplicate)
        cases.append((f"adversarial-{index:04d}", raw))
    return cases


def edge_cases():
    one = {"version": 1, "groups": ["é", "e\u0301"], "classes": ["red", "blue"],
           "partitions": ["right", "left"], "counts": [[499999999, 1], [1, 499999999]],
           "requirements": []}
    rows = [("billion-unconstrained", deepcopy(one))]
    for name, bounds in [
        ("billion-exact", [{"measure": "records", "lower": 500000000, "upper": 500000000}]),
        ("billion-indivisible", [{"measure": "records", "lower": 499999999, "upper": 499999999}]),
        ("zero-records", [{"measure": "records", "upper": 0}]),
        ("zero-groups", [{"measure": "groups", "lower": 0, "upper": 0}]),
        ("inverted-groups", [{"measure": "groups", "lower": 1, "upper": 0}]),
        ("inverted-records", [{"measure": "records", "lower": 1000000000, "upper": 999999999}]),
        ("inverted-class", [{"measure": "class", "class": "red", "lower": 2, "upper": 1}]),
        ("outside-group-total", [{"measure": "groups", "lower": 1000000000}]),
        ("outside-class-total", [{"measure": "class", "class": "red", "lower": 1000000000}]),
        ("repeated-conjunctive", [{"measure": "groups", "lower": 1}, {"measure": "groups", "upper": 0}]),
    ]:
        raw = deepcopy(one)
        raw["requirements"] = [dict(b, id=f"bound-{i}", partition="left") for i, b in enumerate(bounds)]
        rows.append((name, raw))
    rows.append(("eight-empty-allowed", {"version": 1, "groups": ["only"], "classes": ["only"],
                                        "partitions": [f"p{i}" for i in range(8)], "counts": [[1]],
                                        "requirements": []}))
    return rows


def validate_production_report(raw, result, expected=None):
    status = result["status"]
    if status not in {"FEASIBLE", "INFEASIBLE", "UNKNOWN"}:
        raise AssertionError(f"unexpected status {status}")
    if expected is not None and status != "UNKNOWN" and status != expected:
        raise AssertionError(f"false decided status: {status} versus {expected}")
    if status == "FEASIBLE" and not direct_valid(raw, assignment_indices(raw, result.get("assignment"))):
        raise AssertionError("invalid production assignment")


def verify_core(raw, result):
    """Independently enumerate each claimed core and every retained deletion."""
    if result["status"] != "INFEASIBLE":
        raise AssertionError("core requested for non-infeasible result")
    detail = result["explanation"]
    ids = detail["requirement_ids"]
    if len(set(ids)) != len(ids) or not set(ids) <= {r["id"] for r in raw["requirements"]}:
        raise AssertionError("invalid core IDs")
    reduced = dict(raw, requirements=[r for r in raw["requirements"] if r["id"] in ids])
    if cartesian_oracle(reduced)["status"] != "INFEASIBLE":
        raise AssertionError("reported sufficient core is feasible")
    if detail["minimal"]:
        if not detail["complete"]:
            raise AssertionError("minimal core marked incomplete")
        witnesses = detail["removal_witnesses"]
        if len(witnesses) != len(ids) or {w["removed"] for w in witnesses} != set(ids):
            raise AssertionError("incomplete minimality witnesses")
        for removed in ids:
            smaller = dict(raw, requirements=[r for r in reduced["requirements"] if r["id"] != removed])
            if cartesian_oracle(smaller)["status"] != "FEASIBLE":
                raise AssertionError("claimed core is not deletion-minimal")
            witness = next(w for w in witnesses if w["removed"] == removed)
            if not direct_valid(smaller, assignment_indices(smaller, witness["assignment"])):
                raise AssertionError("invalid core removal witness")
    return detail


def fresh_milp(raw, timeout=5.0):
    """Optional developer oracle, independently modeled with binary x[g,p].

    Numerical HiGHS infeasibility is not a portable exact proof certificate.
    No SciPy dependency is imported in ordinary tests or runtime paths.
    """
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp
    n, k = len(raw["groups"]), len(raw["partitions"])
    matrix, lower, upper = [], [], []
    for g in range(n):
        row = [0] * (n * k)
        for p in range(k):
            row[g * k + p] = 1
        matrix.append(row)
        lower.append(1)
        upper.append(1)
    for req in raw["requirements"]:
        p = raw["partitions"].index(req["partition"])
        weights = ([1] * n if req["measure"] == "groups" else
                   [sum(r) for r in raw["counts"]] if req["measure"] == "records" else
                   [r[raw["classes"].index(req["class"])] for r in raw["counts"]])
        row = [0] * (n * k)
        for g, value in enumerate(weights):
            row[g * k + p] = value
        matrix.append(row)
        lower.append(req.get("lower", 0))
        upper.append(req.get("upper", sum(weights)))
    result = milp(np.zeros(n*k), integrality=np.ones(n*k), bounds=Bounds(0, 1),
                  constraints=LinearConstraint(np.asarray(matrix, dtype=float), lower, upper),
                  options={"time_limit": timeout})
    if result.x is not None:
        assignment = [int(np.argmax(result.x[g*k:(g+1)*k])) for g in range(n)]
        if direct_valid(raw, assignment):
            return {"status": "FEASIBLE", "assignment": assignment, "highs_status": int(result.status)}
    return {"status": "INFEASIBLE" if result.status == 2 else "UNKNOWN", "highs_status": int(result.status)}


def load_jsonl(path):
    rows = [json.loads(line) for line in path.read_text().splitlines() if line]
    if len({r["id"] for r in rows}) != len(rows):
        raise ValueError("duplicate evidence row IDs")
    return {r["id"]: r for r in rows}


def dump_json(path, value):
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def run_cohort(args):
    from foldcontract.model import ValidationError, problem_from_dict
    from foldcontract.solver import solve
    source_paths = [ROOT / "src/foldcontract" / (name + ".py") for name in ("model", "checker", "solver", "explain")]
    source_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}
    cohort_bytes = args.cohort.read_bytes()
    oracle_bytes = args.reference.read_bytes()
    if hashlib.sha256(cohort_bytes).hexdigest() != COHORT_SHA256:
        raise ValueError("frozen cohort SHA256 mismatch")
    if hashlib.sha256(oracle_bytes).hexdigest() != ORACLE_SHA256:
        raise ValueError("frozen independent oracle SHA256 mismatch")
    if hashlib.sha256(args.research.read_bytes()).hexdigest() != RESEARCH_SHA256:
        raise ValueError("frozen research probe SHA256 mismatch")
    cohort = json.loads(cohort_bytes)
    reference, research = load_jsonl(args.reference), load_jsonl(args.research)
    ids = [r["id"] for r in cohort]
    if len(ids) != 752 or len(set(ids)) != 752 or set(ids) != set(reference) or set(ids) != set(research):
        raise ValueError("the complete 752-row cohort and evidence are required")
    results, mismatches, research_unknown = [], [], []
    for original in cohort:
        raw = translate_frozen(original)
        old, independent = research[original["id"]], reference[original["id"]]
        expected = independent["status"].upper()
        if expected == "FEASIBLE" and not direct_valid(raw, independent["assignment"]):
            raise AssertionError("invalid stored independent witness")
        old_status = old["solver"]["status"].upper()
        if old_status == "UNKNOWN":
            research_unknown.append(original["id"])
        if old_status == "FEASIBLE" and not direct_valid(raw, old["solver"]["assignment"]):
            raise AssertionError("invalid stored research witness")
        if "sgkf" in old:
            for attempt in old["sgkf"]:
                if direct_valid(raw, attempt["assignment"]) != attempt["valid"]:
                    raise AssertionError("incorrect stored SGKF validity flag")
        row = {"id": original["id"], "groups": len(raw["groups"]), "reference_status": expected,
               "reference_method": independent["method"], "research_status": old_status}
        if original["id"].startswith("small-") or original["id"] == "odd-cycle":
            fresh = cartesian_oracle(raw)
            row["fresh_enumeration"] = fresh["status"]
            if fresh["status"] != expected:
                mismatches.append({"id": original["id"], "kind": "fresh enumeration"})
        started = time.monotonic()
        if len(raw["groups"]) > 128:
            try:
                problem_from_dict(raw)
            except ValidationError as error:
                row.update(status="UNSUPPORTED", reason="group admission limit 128", validation_code=error.code)
            else:
                raise AssertionError("production accepted an out-of-profile group count")
        else:
            problem = problem_from_dict(raw)
            report = solve(problem, max_nodes=args.max_nodes, deadline=time.monotonic() + args.seconds)
            row.update(status=report["status"], reason=report["reason"], nodes=report["nodes"],
                       problem_digest=problem.digest)
            if "assignment" in report:
                row["assignment"] = report["assignment"]
            try:
                validate_production_report(raw, report, expected)
            except AssertionError as error:
                mismatches.append({"id": original["id"], "kind": str(error)})
        row["production_seconds"] = time.monotonic() - started
        if args.fresh_milp:
            started = time.monotonic()
            row["fresh_milp"] = fresh_milp(raw, args.seconds)
            row["fresh_milp_seconds"] = time.monotonic() - started
            if row["fresh_milp"]["status"] not in ("UNKNOWN", expected):
                mismatches.append({"id": original["id"], "kind": "fresh MILP"})
        results.append(row)
        if len(results) % 100 == 0:
            print(f"Replayed {len(results)}/752", flush=True)
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    (out / "oracle-production-results.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in results))
    groups = {}
    for family in ["small", "medium", "stress", "historical", "odd"]:
        selected = [r for r in results if r["id"].startswith(family)]
        groups[family] = {"cases": len(selected), "statuses": dict(Counter(r["status"] for r in selected)),
                          "production_total_seconds": sum(r["production_seconds"] for r in selected)}
    if source_hashes != {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}:
        raise RuntimeError("production source changed during replay; rerun on a stable snapshot")
    summary = {"source_sha256": source_hashes, "reproduction_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "cohort_sha256": COHORT_SHA256, "reference_sha256": ORACLE_SHA256, "cases": len(results),
               "python": platform.python_version(), "max_nodes": args.max_nodes, "seconds_per_solve": args.seconds,
               "statuses": dict(Counter(r["status"] for r in results)), "groups": groups,
               "fresh_cartesian_cases": sum("fresh_enumeration" in r for r in results),
               "mismatches": mismatches, "research_unknown_retained": research_unknown,
               "unsupported_ids": [r["id"] for r in results if r["status"] == "UNSUPPORTED"],
               "production_unknown_ids": [r["id"] for r in results if r["status"] == "UNKNOWN"],
               "notes": ["UNSUPPORTED is an evaluation disposition, not a solver status.",
                         "All 752 original rows are retained; 160-group rows exceed production admission.",
                         "Wall-time outcomes and timings are machine-dependent.",
                         "Stored medium/stress HiGHS infeasibility is numerical, not a formal certificate.",
                         "Production timings include input normalization; Cartesian checks are outside them."]}
    if args.fresh_milp:
        import scipy
        summary["fresh_milp"] = {"scipy": scipy.__version__,
                                 "statuses": dict(Counter(r["fresh_milp"]["status"] for r in results)),
                                 "total_seconds": sum(r["fresh_milp_seconds"] for r in results)}
    dump_json(out / "oracle-production-summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    if mismatches:
        raise AssertionError("production replay disagreed with independent evidence")


def run_adversarial(args):
    from foldcontract.model import problem_from_dict
    from foldcontract.solver import solve
    from foldcontract.explain import explain
    source_paths = [ROOT / "src/foldcontract" / (name + ".py") for name in ("model", "checker", "solver", "explain")]
    source_hashes = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}
    results, status_counts, budget_counts = [], Counter(), Counter()
    cores = 0
    for name, raw in random_cases(args.random_cases) + edge_cases():
        expected = cartesian_oracle(raw)
        problem = problem_from_dict(raw)
        report = solve(problem, max_nodes=100000)
        validate_production_report(raw, report, expected["status"])
        if report["status"] == "UNKNOWN":
            raise AssertionError("small complete-budget run unexpectedly UNKNOWN")
        status_counts[report["status"]] += 1
        budgets = {}
        for budget in (0, 1, 2, 3, 7):
            limited = solve(problem, max_nodes=budget)
            validate_production_report(raw, limited, expected["status"])
            if limited["nodes"] > budget or (budget == 0 and limited["status"] != "UNKNOWN"):
                raise AssertionError("node-budget contract violated")
            budgets[str(budget)] = limited["status"]
            budget_counts[limited["status"]] += 1
        row = {"id": name, "raw": raw, "status": report["status"], "budgets": budgets}
        if report["status"] == "INFEASIBLE" and cores < 150:
            explanation = explain(problem, max_nodes=100000, max_checks=1024)
            core = verify_core(raw, explanation)
            if not core["minimal"]:
                raise AssertionError("small full-budget core not minimal")
            row["core"] = core
            cores += 1
        results.append(row)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "oracle-adversarial-results.jsonl").write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in results))
    if source_hashes != {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths}:
        raise RuntimeError("production source changed during adversarial checks; rerun on a stable snapshot")
    summary = {"source_sha256": source_hashes,
               "reproduction_script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
               "cases": len(results), "random_seed": 2026100501, "statuses": dict(status_counts),
               "budget_calls": len(results) * 5, "budget_statuses": dict(budget_counts),
               "independently_enumerated_minimal_cores": cores, "mismatches": 0}
    dump_json(args.output / "oracle-adversarial-summary.json", summary)
    print(json.dumps(summary, indent=2, sort_keys=True))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("cohort", "adversarial", "both"), default="both")
    parser.add_argument("--cohort", type=Path, default=ROOT / "evidence/oracle-frozen-cohort.json")
    parser.add_argument("--reference", type=Path, default=ROOT / "evidence/oracle-research-independent.jsonl")
    parser.add_argument("--research", type=Path, default=ROOT / "evidence/oracle-research-probe.jsonl")
    parser.add_argument("--output", type=Path, default=ROOT / "evidence")
    parser.add_argument("--max-nodes", type=int, default=100000)
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--random-cases", type=int, default=1200)
    parser.add_argument("--fresh-milp", action="store_true", help="require already-installed SciPy; developer-only")
    args = parser.parse_args(argv)
    if args.max_nodes < 0 or args.seconds <= 0 or args.random_cases < 0:
        parser.error("budgets must be nonnegative and seconds positive")
    if args.mode in ("adversarial", "both"):
        run_adversarial(args)
    if args.mode in ("cohort", "both"):
        run_cohort(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
