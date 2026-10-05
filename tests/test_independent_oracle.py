"""Production contracts checked by independently authored full enumeration.

The oracle reads the original input dictionary and never the production model's
normalized requirements. A larger reproducible run is tools/reproduce_cohort.py.
"""
from copy import deepcopy
import hashlib
from itertools import product
import json
from pathlib import Path
import unittest

from foldcontract.model import ValidationError, problem_from_dict
from foldcontract.solver import solve
from foldcontract.explain import explain
from tools.reproduce_cohort import (
    COHORT_SHA256, assignment_indices, cartesian_oracle, direct_valid,
    edge_cases, random_cases, translate_frozen, validate_production_report,
    verify_core,
)

ROOT = Path(__file__).resolve().parents[1]


class IndependentCartesianTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = random_cases(350) + edge_cases()
        cls.expected = {name: cartesian_oracle(raw)["status"] for name, raw in cls.cases}

    def test_production_matches_original_dictionary_enumeration(self):
        for name, raw in self.cases:
            with self.subTest(case=name):
                report = solve(problem_from_dict(raw), max_nodes=100000)
                self.assertEqual(report["status"], self.expected[name])
                validate_production_report(raw, report, self.expected[name])

    def test_exhaustion_never_becomes_a_false_decided_status(self):
        for name, raw in self.cases:
            problem = problem_from_dict(raw)
            for budget in (0, 1, 2, 3, 7):
                with self.subTest(case=name, budget=budget):
                    report = solve(problem, max_nodes=budget)
                    self.assertLessEqual(report["nodes"], budget)
                    validate_production_report(raw, report, self.expected[name])
                    if budget == 0:
                        self.assertEqual(report["status"], "UNKNOWN")

    def test_cores_and_every_bound_removal_independently_enumerated(self):
        checked = 0
        for name, raw in self.cases:
            if self.expected[name] != "INFEASIBLE":
                continue
            with self.subTest(case=name):
                report = explain(problem_from_dict(raw), max_nodes=100000, max_checks=1024)
                core = verify_core(raw, report)
                self.assertTrue(core["minimal"])
                self.assertTrue(core["complete"])
                checked += 1
            if checked == 50:
                break
        self.assertEqual(checked, 50)

    def test_insufficient_core_budget_retains_sufficient_infeasible_subset(self):
        for name, raw in self.cases:
            if self.expected[name] != "INFEASIBLE":
                continue
            problem = problem_from_dict(raw)
            original = solve(problem, max_nodes=100000)
            for budget in (original["nodes"], original["nodes"] + 1):
                report = explain(problem, max_nodes=budget, max_checks=1024)
                verify_core(raw, report)
                self.assertLessEqual(report["nodes"], budget)
            report = explain(problem, max_nodes=100000, max_checks=0)
            detail = verify_core(raw, report)
            self.assertFalse(detail["minimal"])
            self.assertFalse(detail["complete"])
            break

    def test_raw_oracle_rejects_missing_duplicate_or_extra_groups(self):
        raw = edge_cases()[0][1]
        valid = [{"group": g, "partition": "left"} for g in raw["groups"]]
        self.assertTrue(direct_valid(raw, assignment_indices(raw, valid)))
        for assignment in (valid[:1], valid + valid[:1], valid + [{"group": "extra", "partition": "left"}],
                           [{"group": g, "partition": "absent"} for g in raw["groups"]]):
            self.assertIsNone(assignment_indices(raw, assignment))

    def test_canonical_reordering_preserves_raw_semantics(self):
        for _, raw in self.cases[:80]:
            reordered = deepcopy(raw)
            reordered["groups"].reverse()
            reordered["counts"].reverse()
            reordered["classes"].reverse()
            for row in reordered["counts"]:
                row.reverse()
            reordered["partitions"].reverse()
            reordered["requirements"].reverse()
            first = problem_from_dict(raw)
            second = problem_from_dict(reordered)
            self.assertEqual(first.digest, second.digest)
            report = solve(second)
            validate_production_report(raw, report, cartesian_oracle(raw)["status"])
            self.assertEqual(report, solve(first))


class FrozenTranslationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        data = (ROOT / "evidence/oracle-frozen-cohort.json").read_bytes()
        assert hashlib.sha256(data).hexdigest() == COHORT_SHA256
        cls.cohort = json.loads(data)

    def test_exact_frozen_rows_and_out_of_profile_cases_are_preserved(self):
        self.assertEqual(len(self.cohort), 752)
        out_of_profile = [r for r in self.cohort if len(r["counts"]) > 128]
        self.assertEqual(len(out_of_profile), 10)
        self.assertTrue(all(len(r["counts"]) == 160 for r in out_of_profile))
        for row in out_of_profile:
            with self.assertRaises(ValidationError):
                problem_from_dict(translate_frozen(row))

    def test_appended_total_is_records_not_extra_class(self):
        for row in self.cohort:
            raw = translate_frozen(row)
            c = row["classes"]
            self.assertEqual(len(raw["classes"]), c)
            self.assertEqual(raw["counts"], [r[:c] for r in row["counts"]])
            expected_records = row["k"] if len(row["counts"][0]) == c + 1 else 0
            self.assertEqual(sum(r["measure"] == "records" for r in raw["requirements"]), expected_records)
            self.assertFalse(any(r["measure"] == "groups" for r in raw["requirements"]))

    def test_translation_preserves_each_assignment_on_small_sample(self):
        for row in self.cohort[:20]:
            raw = translate_frozen(row)
            width = len(row["counts"][0])
            for assignment in product(range(row["k"]), repeat=len(row["counts"])):
                totals = [[0] * width for _ in range(row["k"])]
                for g, p in enumerate(assignment):
                    for d, value in enumerate(row["counts"][g]):
                        totals[p][d] += value
                old_valid = all(row["lower"][p][d] <= totals[p][d] <= row["upper"][p][d]
                                for p in range(row["k"]) for d in range(width))
                self.assertEqual(direct_valid(raw, assignment), old_valid)

    def test_corrupt_appended_total_is_not_silently_reinterpreted(self):
        row = deepcopy(next(r for r in self.cohort if r["capacity"]))
        row["counts"][0][-1] += 1
        with self.assertRaisesRegex(ValueError, "record total"):
            translate_frozen(row)


if __name__ == "__main__":
    unittest.main()
