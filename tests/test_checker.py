"""Independent witness verification tests; no solver dependency."""
import copy
import dataclasses
import importlib
import importlib.util
import json
import unittest


def raw_problem():
    return {"version": 1, "groups": ["red", "blue", "green"], "classes": ["a", "b"],
            "partitions": ["test", "train"], "counts": [[2, 1], [1, 0], [0, 3]],
            "requirements": [{"id": "coverage-a", "partition": "test", "measure": "class",
                              "class": "a", "lower": 2},
                             {"id": "size", "partition": "test", "measure": "records", "upper": 3},
                             {"id": "train-groups", "partition": "train", "measure": "groups", "lower": 2}]}


class CheckerTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("foldcontract.checker"),
                             "The independent checker API has not been implemented")
        self.checker = importlib.import_module("foldcontract.checker")
        self.model = importlib.import_module("foldcontract.model")
        self.problem = self.model.problem_from_dict(raw_problem())
        self.assignment = [{"group": "red", "partition": "test"},
                           {"group": "blue", "partition": "train"},
                           {"group": "green", "partition": "train"}]

    def test_valid_witness_has_recomputed_exact_totals(self):
        result = self.checker.check_assignment(self.problem, self.assignment)
        self.assertTrue(result["valid"])
        self.assertEqual(result["violations"], [])
        self.assertEqual(result["totals"], {"test": {"records": 3, "groups": 1, "classes": {"a": 2, "b": 1}},
                                            "train": {"records": 4, "groups": 2, "classes": {"a": 1, "b": 3}}})
        as_dict = {a["group"]: a["partition"] for a in self.assignment}
        self.assertEqual(self.checker.check_assignment(self.problem, as_dict), result)

    def test_missing_extra_duplicate_and_unknown_partition_rejected(self):
        variants = [self.assignment[:-1],
                    self.assignment + [{"group": "unknown", "partition": "test"}],
                    self.assignment + [self.assignment[0]],
                    [{"group": "red", "partition": "unknown"}] + self.assignment[1:]]
        for assignment in variants:
            with self.subTest(assignment=assignment):
                result = self.checker.check_assignment(self.problem, assignment)
                self.assertFalse(result["valid"])
                self.assertTrue(result["violations"])

    def test_malformed_assignment_cannot_crash_or_be_admitted(self):
        for value in [None, "test", (), True, [None], [[]], ["red"], [{"group": "red"}],
                      [{"group": "red", "partition": "test", "extra": 0}],
                      [{"group": [], "partition": "test"}],
                      [{"group": "red", "partition": {}}], {1: "test"}, {"red": True}]:
            with self.subTest(value=value):
                result = self.checker.check_assignment(self.problem, value)
                self.assertFalse(result["valid"])
                self.assertTrue(result["violations"])

    def test_all_named_requirements_checked_with_exact_observed_values(self):
        assignment = {"red": "train", "blue": "test", "green": "test"}
        result = self.checker.check_assignment(self.problem, assignment)
        self.assertFalse(result["valid"])
        violations = {v["requirement_id"]: v for v in result["violations"] if "requirement_id" in v}
        self.assertEqual(set(violations), {"coverage-a", "size", "train-groups"})
        self.assertEqual({key: item["observed"] for key, item in violations.items()},
                         {"coverage-a": 1, "size": 4, "train-groups": 1})
        for item in violations.values():
            self.assertIn("lower", item)
            self.assertIn("upper", item)

    def test_repeated_bounds_conjunction_keeps_individual_names(self):
        raw = raw_problem()
        raw["requirements"].append({"id": "zero-a", "partition": "test", "measure": "class",
                                    "class": "a", "upper": 0})
        p = self.model.problem_from_dict(raw)
        result = self.checker.check_assignment(p, self.assignment)
        self.assertFalse(result["valid"])
        self.assertEqual([v["requirement_id"] for v in result["violations"]], ["zero-a"])

    def test_empty_partition_is_legal_without_explicit_requirements(self):
        raw = raw_problem()
        raw["requirements"] = []
        p = self.model.problem_from_dict(raw)
        result = self.checker.check_assignment(p, {g: "test" for g in p.groups})
        self.assertTrue(result["valid"])
        self.assertEqual(result["totals"]["train"]["records"], 0)

    def test_counts_near_admission_limit_are_exact(self):
        raw = {"version": 1, "groups": ["a", "b"], "classes": ["x"],
               "partitions": ["p", "q"], "counts": [[999_999_999], [1]],
               "requirements": [{"id": "exact", "partition": "p", "measure": "records",
                                  "lower": 1_000_000_000, "upper": 1_000_000_000}]}
        p = self.model.problem_from_dict(raw)
        result = self.checker.check_assignment(p, {"a": "p", "b": "p"})
        self.assertTrue(result["valid"])
        self.assertEqual(result["totals"]["p"]["records"], 1_000_000_000)

    def test_check_report_validates_only_feasible_matching_digest(self):
        report = {"status": "FEASIBLE", "problem_digest": self.problem.digest,
                  "assignment": self.assignment, "totals": {"fabricated": 7}}
        self.assertTrue(self.checker.check_report(self.problem, report)["valid"])
        for change in [{"status": "INFEASIBLE"}, {"status": "UNKNOWN"}, {"status": None},
                       {"problem_digest": "0" * 64}, {"assignment": self.assignment[:-1]}]:
            forged = copy.deepcopy(report)
            forged.update(change)
            with self.subTest(change=change):
                self.assertFalse(self.checker.check_report(self.problem, forged)["valid"])
        for field in ("status", "problem_digest", "assignment"):
            forged = copy.deepcopy(report)
            del forged[field]
            self.assertFalse(self.checker.check_report(self.problem, forged)["valid"])
        for malformed in [None, [], "FEASIBLE", 1]:
            self.assertFalse(self.checker.check_report(self.problem, malformed)["valid"])

    def test_check_does_not_mutate_problem_report_or_assignment(self):
        report = {"status": "FEASIBLE", "problem_digest": self.problem.digest,
                  "assignment": self.assignment}
        before = copy.deepcopy(report)
        p_before = self.model.problem_to_dict(self.problem)
        self.checker.check_report(self.problem, report)
        self.assertEqual(report, before)
        self.assertEqual(self.model.problem_to_dict(self.problem), p_before)

    def test_checker_rejects_forged_problem_objects(self):
        malformed = [dataclasses.replace(self.problem, digest="0" * 64),
                     dataclasses.replace(self.problem, counts=((-1, 0), (0, 3), (2, 1))),
                     dataclasses.replace(self.problem, groups=list(self.problem.groups))]
        for problem in malformed:
            with self.subTest(problem=problem), self.assertRaises(self.model.ValidationError):
                self.checker.check_assignment(problem, self.assignment)
            report = {"status": "FEASIBLE", "problem_digest": problem.digest,
                      "assignment": self.assignment}
            with self.assertRaises(self.model.ValidationError):
                self.checker.check_report(problem, report)

    def test_assignment_size_rejected_before_copying_or_iteration(self):
        for oversized in [[self.assignment[0]] * 100_000,
                          {f"extra-{i}": "test" for i in range(1000)}]:
            result = self.checker.check_assignment(self.problem, oversized)
            self.assertFalse(result["valid"])
            self.assertEqual([v["code"] for v in result["violations"]], ["ASSIGNMENT_SIZE"])

    def test_assignment_identifiers_are_bounded_without_echoing_invalid_values(self):
        for bad in ["x" * 129, "é" * 65, "\ud800", ""]:
            for key in ("group", "partition"):
                assignment = copy.deepcopy(self.assignment)
                assignment[0][key] = bad
                with self.subTest(key=key, value=repr(bad)):
                    result = self.checker.check_assignment(self.problem, assignment)
                    self.assertFalse(result["valid"])
                    self.assertEqual(result["violations"][0]["code"], "INVALID_ENTRY")
                    self.assertNotIn(bad, [v.get(key) for v in result["violations"]])
                    self.assertLess(len(json.dumps(result).encode("utf-8")), 2048)


if __name__ == "__main__":
    unittest.main()
