"""Independent release-review regressions and raw-schema exhaustive oracles.

Written separately from the implementation tests. The oracle does not call the
production checker, constraint normalizer, propagator, or requirement reducer.
"""
import ast
import copy
import dataclasses
import itertools
import json
import os
from pathlib import Path
import random
import tempfile
import subprocess
import time
import unittest
from unittest import mock

import foldcontract
from foldcontract import runtime
from foldcontract.adapter import aggregate_rows, split_indices
from foldcontract.checker import check_assignment
from foldcontract.cli import _atomic_write
from foldcontract.explain import explain
from foldcontract.model import ValidationError, parse_problem, problem_from_dict
from foldcontract.solver import solve

BASE = {"version": 1, "groups": ["b", "a"], "classes": ["c"],
        "partitions": ["right", "left"], "counts": [[1], [2]], "requirements": []}
DATA = json.dumps(BASE).encode()


def raw_valid(raw, assignment):
    if set(assignment) != set(raw["groups"]):
        return False
    if any(p not in raw["partitions"] for p in assignment.values()):
        return False
    for requirement in raw["requirements"]:
        if requirement["measure"] == "groups":
            weights = [1] * len(raw["groups"])
        elif requirement["measure"] == "records":
            weights = [sum(row) for row in raw["counts"]]
        else:
            index = raw["classes"].index(requirement["class"])
            weights = [row[index] for row in raw["counts"]]
        observed = sum(weight for group, weight in zip(raw["groups"], weights)
                       if assignment[group] == requirement["partition"])
        if not requirement.get("lower", 0) <= observed <= requirement.get("upper", sum(weights)):
            return False
    return True


def exhaustive(raw):
    for partitions in itertools.product(raw["partitions"], repeat=len(raw["groups"])):
        assignment = dict(zip(raw["groups"], partitions))
        if raw_valid(raw, assignment):
            return assignment
    return None


def witness(report):
    entries = report["assignment"]
    if len({entry["group"] for entry in entries}) != len(entries):
        raise AssertionError("Duplicate witness groups")
    return {entry["group"]: entry["partition"] for entry in entries}


def review_cases(count=1024):
    rng = random.Random(9015102026)
    for case in range(count):
        n, c, k = rng.randint(1, 7), rng.randint(1, 3), rng.randint(2, 3)
        counts = [[rng.randrange(4) for _ in range(c)] for _ in range(n)]
        for row in counts:
            if not any(row):
                row[rng.randrange(c)] = 1
        for j in range(c):
            if not any(row[j] for row in counts):
                counts[rng.randrange(n)][j] = 1
        if case % 61 == 0:
            counts[0][0] += 1_000_000_000 - sum(map(sum, counts))
        raw = {"version": 1, "groups": [f"g{i}" for i in reversed(range(n))],
               "classes": [f"c{i}" for i in reversed(range(c))],
               "partitions": [f"p{i}" for i in reversed(range(k))],
               "counts": counts, "requirements": []}
        for j in range(rng.randrange(13)):
            measure = rng.choice(("class", "records", "groups"))
            req = {"id": f"r{j}", "partition": rng.choice(raw["partitions"]), "measure": measure}
            if measure == "class":
                req["class"] = rng.choice(raw["classes"])
            endpoint = rng.choice(("lower", "upper", "both"))
            if endpoint in ("lower", "both"):
                req["lower"] = rng.randrange(9)
            if endpoint in ("upper", "both"):
                req["upper"] = rng.randrange(9)
            raw["requirements"].append(req)
        yield case, raw


class IndependentSolverReview(unittest.TestCase):
    def test_1024_raw_schema_cases_and_4096_budget_runs(self):
        for case, raw in review_cases():
            expected = "FEASIBLE" if exhaustive(raw) is not None else "INFEASIBLE"
            problem = problem_from_dict(raw)
            full = solve(problem)
            self.assertEqual(full["status"], expected, (case, raw, full))
            if expected == "FEASIBLE":
                self.assertTrue(raw_valid(raw, witness(full)), case)
            for budget in (0, 1, 2, 7):
                limited = solve(problem, max_nodes=budget)
                self.assertLessEqual(limited["nodes"], budget, case)
                self.assertIn(limited["status"], ("UNKNOWN", expected), (case, budget, limited))
                if limited["status"] == "FEASIBLE":
                    self.assertTrue(raw_valid(raw, witness(limited)), (case, budget))

    def test_128_cores_and_removal_witnesses_against_raw_oracle(self):
        seen = 0
        for case, raw in review_cases():
            if exhaustive(raw) is not None:
                continue
            result = explain(problem_from_dict(raw))
            detail = result["explanation"]
            self.assertEqual(result["status"], "INFEASIBLE", case)
            self.assertTrue(detail["minimal"], (case, detail))
            core = copy.deepcopy(raw)
            core["requirements"] = [r for r in raw["requirements"] if r["id"] in detail["requirement_ids"]]
            self.assertIsNone(exhaustive(core), case)
            self.assertEqual(set(detail["requirement_ids"]), {w["removed"] for w in detail["removal_witnesses"]})
            for item in detail["removal_witnesses"]:
                smaller = copy.deepcopy(core)
                smaller["requirements"] = [r for r in smaller["requirements"] if r["id"] != item["removed"]]
                self.assertTrue(raw_valid(smaller, witness(item)), (case, item))
            seen += 1
            if seen == 128:
                break
        self.assertEqual(seen, 128)

    def test_infeasible_dataclass_bypass_rejected_before_search(self):
        raw = copy.deepcopy(BASE)
        raw["requirements"] = [{"id": "impossible", "partition": "left", "measure": "groups", "lower": 3}]
        problem = dataclasses.replace(problem_from_dict(raw), digest="f" * 64)
        for operation in (solve, explain):
            with self.subTest(operation=operation.__name__), self.assertRaises(ValidationError):
                operation(problem)

    def test_extremely_large_deadline_is_validation_error(self):
        with self.assertRaises(ValidationError):
            solve(problem_from_dict(BASE), deadline=10**1000)


class AdmissionAndAdapterReview(unittest.TestCase):
    def test_lexical_preflight_runs_before_json_decoder(self):
        invalid = (b"[" * 100_000, b'{"version":' + b"9" * 100_000 + b"}")
        for data in invalid:
            with self.subTest(data=data[:20]), mock.patch("foldcontract.model.json.loads", side_effect=AssertionError("decoder ran")):
                with self.assertRaises(ValidationError):
                    parse_problem(data)

    def test_escaped_delimiters_and_numerals_are_ordinary_ids(self):
        raw = copy.deepcopy(BASE)
        raw["groups"] = ['\\"[}{]1e9999999999999999999', 'e\u0301']
        raw["partitions"] = ["é", "e\u0301"]
        parsed = parse_problem(json.dumps(raw))
        self.assertEqual(set(parsed.groups), set(raw["groups"]))
        self.assertEqual(set(parsed.partitions), set(raw["partitions"]))

    def test_named_conjunction_and_order_independence(self):
        raw = copy.deepcopy(BASE)
        raw["requirements"] = [{"id": "upper", "partition": "left", "measure": "records", "upper": 1},
                               {"id": "lower", "partition": "left", "measure": "records", "lower": 2}]
        a = problem_from_dict(raw)
        raw["groups"].reverse(); raw["counts"].reverse(); raw["partitions"].reverse(); raw["requirements"].reverse()
        b = problem_from_dict(raw)
        self.assertEqual(a, b)
        self.assertEqual(solve(a), solve(b))
        self.assertEqual(solve(a)["status"], "INFEASIBLE")
        self.assertEqual(set(explain(a)["explanation"]["requirement_ids"]), {"upper", "lower"})

    def test_adapter_preserves_reordered_rows_and_group_disjointness(self):
        y = ["red", "blue", "red", "blue", "red"]
        groups = ["b", "a", "a", "c", "b"]
        problem = aggregate_rows(y, groups, ["test", "train"])
        report = {"status": "FEASIBLE", "problem_digest": problem.digest,
                  "assignment": {"a": "train", "b": "test", "c": "train"}}
        order = [4, 2, 0, 3, 1]
        labels = [y[i] for i in order]; ids = [groups[i] for i in order]
        splits = split_indices(problem, report, labels, ids)
        self.assertEqual(splits[0], ([1, 3, 4], [0, 2]))
        for train, test in splits:
            self.assertFalse({ids[i] for i in train} & {ids[i] for i in test})
            self.assertEqual(sorted(train + test), list(range(len(y))))
        empty_report = dict(report, assignment={g: "test" for g in problem.groups})
        self.assertTrue(check_assignment(problem, empty_report["assignment"])["valid"])
        with self.assertRaises(ValidationError):
            split_indices(problem, empty_report, y, groups)


class SupervisorReview(unittest.TestCase):
    def envelope(self, report):
        return json.dumps({"protocol": 1, "limits": {"memory_enforced": True,
            "memory_limit_bytes": 512 * 1024**2}, "report": report}).encode()

    def test_malformed_infeasible_protocol_is_error(self):
        for report in ({"status": "INFEASIBLE", "nodes": 0},
                       {"status": "INFEASIBLE", "nodes": 1, "reason": "exhausted_search",
                        "engine_version": "0.1.0a1", "problem_digest": "f" * 64}):
            with self.subTest(report=report), mock.patch.object(runtime, "_exchange", return_value=(self.envelope(report), None, 0)):
                self.assertEqual(runtime.run_bytes(DATA)["status"], "ERROR")

    def test_malformed_explanation_protocol_is_error(self):
        raw = copy.deepcopy(BASE)
        raw["requirements"] = [{"id": "impossible", "partition": "left", "measure": "groups", "lower": 3}]
        data = json.dumps(raw).encode()
        correct = explain(problem_from_dict(raw))
        malformed = []
        missing = copy.deepcopy(correct); del missing["explanation"]; malformed.append(missing)
        wrong_type = copy.deepcopy(correct); wrong_type["explanation"] = "bogus"; malformed.append(wrong_type)
        no_witness = copy.deepcopy(correct); no_witness["explanation"]["removal_witnesses"] = []; malformed.append(no_witness)
        unknown_id = copy.deepcopy(correct); unknown_id["explanation"]["requirement_ids"] = ["absent"]; malformed.append(unknown_id)
        for report in malformed:
            with self.subTest(report=report), mock.patch.object(runtime, "_exchange", return_value=(self.envelope(report), None, 0)):
                self.assertEqual(runtime.run_bytes(data, operation="explain")["status"], "ERROR")

    def test_explain_preserves_original_infeasible_after_reduction_timeout(self):
        raw = copy.deepcopy(BASE)
        raw["requirements"] = [{"id": "impossible", "partition": "left", "measure": "groups", "lower": 3}]
        data = json.dumps(raw).encode()
        report = explain(problem_from_dict(raw), max_checks=0)
        report["explanation"]["reason"] = "timeout"
        now = [0.0]
        def completed_worker(*args):
            now[0] = 1.01
            return self.envelope(report), None, 0
        with mock.patch.object(runtime.time, "monotonic", side_effect=lambda: now[0]), mock.patch.object(runtime, "_exchange", side_effect=completed_worker):
            result = runtime.run_bytes(data, operation="explain", timeout=1)
        self.assertEqual(result["status"], "INFEASIBLE", result)
        self.assertFalse(result["explanation"]["minimal"])

    def test_bad_public_options_never_escape_structured_validation(self):
        for options in ({"operation": []}, {"operation": {}}, {"timeout": 10**1000}):
            with self.subTest(options=options):
                self.assertEqual(runtime.run_bytes(DATA, **options)["status"], "VALIDATION_ERROR")

    def test_unexpected_worker_exception_is_error_not_unknown(self):
        with mock.patch.object(runtime, "_exchange", side_effect=RuntimeError("unexpected")):
            result = runtime.run_bytes(DATA)
        self.assertEqual((result["status"], result["reason"]), ("ERROR", "SUPERVISOR_FAILURE"))

    def test_report_depth_and_duplicate_keys_rejected(self):
        for report in (b"[" * 100_000, b'{"status":"FEASIBLE","status":"FEASIBLE"}'):
            result = runtime.run_check_bytes(DATA, report)
            self.assertEqual(result["status"], "VALIDATION_ERROR", result)


class IsolationReview(unittest.TestCase):
    def test_runtime_source_has_no_network_pickle_or_dynamic_execution(self):
        source_root = Path(foldcontract.__file__).resolve().parent
        self.assertTrue((source_root / "solver.py").is_file(), "Audit must inspect the imported package")
        forbidden_imports = {"socket", "ssl", "http", "urllib", "requests", "pickle", "cloudpickle", "dill", "importlib"}
        forbidden_calls = {"eval", "exec", "__import__", "compile"}
        for source in source_root.glob("*.py"):
            tree = ast.parse(source.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    self.assertFalse({alias.name.split(".")[0] for alias in node.names} & forbidden_imports, source)
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(node.module.split(".")[0], forbidden_imports, source)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, forbidden_calls, source)

    def test_isolated_worker_ignores_cwd_and_pythonpath_code(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            marker = root / "executed"
            malicious = f"open({str(marker)!r}, 'w').write('executed')\nraise RuntimeError('input-adjacent module ran')\n"
            (root / "json.py").write_text(malicious)
            (root / "sitecustomize.py").write_text(malicious)
            (root / "usercustomize.py").write_text(malicious)
            command = runtime._worker_command("solve", 100000, 1024, time.monotonic() + 10, 512 * 1024**2, 0)
            completed = subprocess.run(command, input=DATA, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       cwd=root, env=dict(os.environ, PYTHONPATH=str(root)), timeout=15)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(json.loads(completed.stdout)["report"]["status"], "FEASIBLE")
            self.assertFalse(marker.exists())


class PublicationReview(unittest.TestCase):
    def test_input_hardlink_and_parent_symlink_aliases_cannot_be_clobbered(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "input.json"; source.write_bytes(DATA)
            alias = root / "alias.json"; os.link(source, alias)
            parent_alias = root / "parent"; parent_alias.symlink_to(root, target_is_directory=True)
            for target in (source, alias, parent_alias / source.name):
                with self.subTest(target=target), self.assertRaises(FileExistsError):
                    _atomic_write(target, b"replacement")
            self.assertEqual(source.read_bytes(), DATA)
            self.assertEqual(alias.read_bytes(), DATA)
            self.assertEqual(list(root.glob(".foldcontract-*")), [])

    def test_racing_symlink_output_wins_without_following(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            input_path = root / "input.json"; input_path.write_bytes(DATA)
            output_path = root / "output.json"
            real_link = os.link
            def competitor(source, target):
                Path(target).symlink_to(input_path)
                return real_link(source, target)
            with mock.patch("foldcontract.cli.os.link", side_effect=competitor), self.assertRaises(FileExistsError):
                _atomic_write(output_path, b"replacement")
            self.assertTrue(output_path.is_symlink())
            self.assertEqual(input_path.read_bytes(), DATA)
            self.assertEqual(list(root.glob(".foldcontract-*")), [])


if __name__ == "__main__":
    unittest.main()
