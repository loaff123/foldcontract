"""Behavioral coverage for explicit single-label row adapters."""

import copy
from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import unittest
from collections.abc import Sequence

from foldcontract.adapter import (
    ReportCrossValidator,
    aggregate_rows,
    kfold_problem,
    split_indices,
)
from foldcontract.checker import check_report
from foldcontract.model import Requirement, ValidationError, parse_problem


def report_for(problem, assignment):
    return {
        "status": "FEASIBLE",
        "problem_digest": problem.digest,
        "assignment": [{"group": group, "partition": partition}
                       for group, partition in assignment.items()],
    }


class TooManyRows(Sequence):
    """An oversized source whose row values must never be visited."""

    def __len__(self):
        return 1_000_001

    def __getitem__(self, index):
        raise AssertionError("oversized rows must be rejected before iteration")


class AggregateRowsTests(unittest.TestCase):
    def test_aggregates_rows_into_exact_canonical_counts(self):
        problem = aggregate_rows(
            ["b", "a", "b", "a", "a"],
            ["z", "x", "z", "x", "z"],
            ["test", "train"],
        )
        self.assertEqual(problem.groups, ("x", "z"))
        self.assertEqual(problem.classes, ("a", "b"))
        self.assertEqual(problem.counts, ((2, 0), (1, 2)))
        self.assertEqual(problem.requirements, ())

    def test_does_not_normalize_case_whitespace_or_unicode(self):
        labels = ["A", "a", " a", "\u00e9", "e\u0301"]
        problem = aggregate_rows(labels, labels, ("left", "right"))
        self.assertEqual(problem.classes, tuple(sorted(labels)))
        self.assertEqual(problem.groups, tuple(sorted(labels)))
        self.assertEqual(sum(map(sum, problem.counts)), 5)

    def test_preserves_explicit_named_requirements(self):
        requirements = [{"id": "one-test", "partition": "test",
                         "measure": "records", "lower": 1}]
        before = copy.deepcopy(requirements)
        problem = aggregate_rows(["a", "a"], ["g1", "g2"],
                                 ["test", "train"], requirements)
        self.assertEqual([item.id for item in problem.requirements], ["one-test"])
        self.assertEqual(requirements, before)

    def test_same_source_multiset_has_same_digest(self):
        first = aggregate_rows(["a", "b", "a"], ["g", "h", "h"], ["B", "A"])
        second = aggregate_rows(["a", "a", "b"], ["h", "g", "h"], ["A", "B"])
        self.assertEqual(first.digest, second.digest)

    def test_empty_rows_are_rejected(self):
        with self.assertRaises(ValidationError):
            aggregate_rows([], [], ["a", "b"])

    def test_mismatched_source_lengths_are_rejected(self):
        with self.assertRaises(ValidationError):
            aggregate_rows(["a"], ["g", "h"], ["a", "b"])

    def test_labels_and_groups_are_strictly_single_strings(self):
        for value in (0, True, 1.0, None, ["a"], ("a",), {"a"}):
            for target in ("label", "group"):
                with self.subTest(value=value, target=target):
                    y, groups = ([value], ["g"]) if target == "label" else (["a"], [value])
                    with self.assertRaises(ValidationError):
                        aggregate_rows(y, groups, ["a", "b"])

    def test_non_sequence_and_text_containers_are_rejected(self):
        for value in ("a", b"a", iter(["a"]), {"a": 1}, {"a"}, None):
            with self.subTest(value=type(value).__name__):
                with self.assertRaises(ValidationError):
                    aggregate_rows(value, ["g"], ["a", "b"])

    def test_partition_and_requirement_inputs_are_finite_sequences(self):
        for partitions in ("pq", {"p", "q"}, iter(["p", "q"]), None):
            with self.subTest(partitions=type(partitions).__name__):
                with self.assertRaises(ValidationError):
                    aggregate_rows(["a"], ["g"], partitions)
        for requirements in ({}, iter([]), "", None):
            with self.subTest(requirements=type(requirements).__name__):
                with self.assertRaises(ValidationError):
                    aggregate_rows(["a"], ["g"], ["p", "q"], requirements)

    def test_row_cap_is_checked_before_source_access(self):
        with self.assertRaises(ValidationError):
            aggregate_rows(TooManyRows(), TooManyRows(), ["a", "b"])

    def test_core_limits_apply_without_silent_truncation(self):
        cases = [
            (["a"] * 129, [str(i) for i in range(129)], ["a", "b"]),
            ([str(i) for i in range(33)], ["g"] * 33, ["a", "b"]),
            (["a"], ["g"], [str(i) for i in range(9)]),
            (["a"], ["g"], ["a", "a"]),
            ([""], ["g"], ["a", "b"]),
            (["a"], ["\u00e9" * 65], ["a", "b"]),
        ]
        for y, groups, partitions in cases:
            with self.subTest(y=y[:2], groups=groups[:2], partitions=partitions):
                with self.assertRaises(ValidationError):
                    aggregate_rows(y, groups, partitions)

    def test_requirement_validation_remains_core_validation(self):
        with self.assertRaises(ValidationError):
            aggregate_rows(["a"], ["g"], ["p", "q"], [
                {"id": "bad", "partition": "missing", "measure": "records", "lower": 1}
            ])

    def test_requirement_objects_preserve_the_same_named_bounds(self):
        requirement = Requirement("one", "p", "records", None, 1, 2)
        problem = aggregate_rows(["a", "a"], ["g", "h"], ["p", "q"], [requirement])
        self.assertEqual(problem.requirements, (requirement,))

    def test_invalid_requirement_objects_are_not_silently_repaired(self):
        requirement = Requirement("bad", "p", "records", "a", 0, 2)
        with self.assertRaises(ValidationError):
            aggregate_rows(["a", "a"], ["g", "h"], ["p", "q"], [requirement])


class KFoldProblemTests(unittest.TestCase):
    def test_explicit_preset_names_all_group_and_class_minima(self):
        problem = kfold_problem(["a", "b", "a", "b"], ["1", "1", "2", "2"], 2, 1)
        self.assertEqual(problem.partitions, ("fold_0", "fold_1"))
        self.assertEqual(len(problem.requirements), 6)
        self.assertEqual(len({r.id for r in problem.requirements}), 6)
        for partition in problem.partitions:
            reqs = [r for r in problem.requirements if r.partition == partition]
            self.assertEqual({r.measure for r in reqs}, {"groups", "class"})
            self.assertEqual({r.class_name for r in reqs if r.measure == "class"}, {"a", "b"})
            self.assertTrue(all(r.lower == 1 for r in reqs))

    def test_zero_class_minimum_still_requests_one_group_per_fold(self):
        problem = kfold_problem(["a", "a"], ["1", "2"], 2, 0)
        self.assertEqual(len(problem.requirements), 4)
        self.assertEqual([r.lower for r in problem.requirements if r.measure == "class"], [0, 0])
        self.assertEqual([r.lower for r in problem.requirements if r.measure == "groups"], [1, 1])

    def test_impossible_minimum_is_well_formed_not_silently_relaxed(self):
        problem = kfold_problem(["a"], ["g"], 2, 1_000_000_000)
        self.assertEqual([r.lower for r in problem.requirements if r.measure == "class"],
                         [1_000_000_000, 1_000_000_000])

    def test_max_length_labels_do_not_overflow_generated_requirement_ids(self):
        problem = kfold_problem(["a" * 128, "b" * 128], ["g1", "g2"], 2)
        self.assertTrue(all(len(r.id.encode("utf-8")) <= 128 for r in problem.requirements))

    def test_invalid_preset_arguments_are_rejected(self):
        for value in (True, 1, 9, 2.0, "2", None):
            with self.subTest(n_splits=value):
                with self.assertRaises(ValidationError):
                    kfold_problem(["a"], ["g"], value)
        for value in (True, -1, 1_000_000_001, 1.0, "1", None):
            with self.subTest(min_class_count=value):
                with self.assertRaises(ValidationError):
                    kfold_problem(["a"], ["g"], 2, value)


class SplitIndicesTests(unittest.TestCase):
    def setUp(self):
        self.y = ["b", "a", "b", "a", "a", "b"]
        self.groups = ["g2", "g1", "g3", "g2", "g1", "g3"]
        self.problem = aggregate_rows(self.y, self.groups, ["C", "A", "B"])
        self.assignment = {"g1": "B", "g2": "A", "g3": "C"}
        self.report = report_for(self.problem, self.assignment)

    def test_original_row_order_with_canonical_partition_order(self):
        splits = split_indices(self.problem, self.report, self.y, self.groups)
        self.assertEqual(splits, [([1, 2, 4, 5], [0, 3]),
                                  ([0, 2, 3, 5], [1, 4]),
                                  ([0, 1, 3, 4], [2, 5])])

    def test_all_splits_are_disjoint_groupwise_and_cover_all_rows(self):
        for train, test in split_indices(self.problem, self.report, self.y, self.groups):
            self.assertFalse(set(train) & set(test))
            self.assertEqual(set(train) | set(test), set(range(len(self.y))))
            self.assertFalse({self.groups[i] for i in train} & {self.groups[i] for i in test})

    def test_source_reordering_is_allowed_and_new_indices_are_correct(self):
        order = [5, 4, 3, 2, 1, 0]
        y = [self.y[i] for i in order]
        groups = [self.groups[i] for i in order]
        result = split_indices(self.problem, self.report, y, groups)
        self.assertEqual(result[0][1], [2, 5])
        self.assertEqual(result[1][1], [1, 4])

    def test_rejects_changed_counts_and_group_mapping(self):
        cases = [
            (["a"] + self.y[1:], self.groups),
            (self.y, ["other"] + self.groups[1:]),
            (self.y[:-1], self.groups[:-1]),
            (self.y, ["g1"] + self.groups[1:]),
        ]
        for y, groups in cases:
            with self.subTest(y=y, groups=groups):
                with self.assertRaises(ValidationError):
                    split_indices(self.problem, self.report, y, groups)

    def test_rejects_infeasible_unknown_digest_mismatch_and_tampered_witness(self):
        bad_reports = [
            None,
            {},
            {**self.report, "status": "INFEASIBLE"},
            {**self.report, "status": "UNKNOWN"},
            {**self.report, "problem_digest": "0" * 64},
            {**self.report, "assignment": self.report["assignment"][:-1]},
            {**self.report, "assignment": self.report["assignment"] * 2},
            {**self.report, "assignment": [{"group": "g1", "partition": "missing"}]},
        ]
        for report in bad_reports:
            with self.subTest(report=report):
                with self.assertRaises(ValidationError):
                    split_indices(self.problem, report, self.y, self.groups)

    def test_checks_actual_requirements_not_only_digest_or_totals(self):
        problem = aggregate_rows(self.y, self.groups, ["A", "B", "C"], [
            {"id": "three-in-A", "partition": "A", "measure": "records", "lower": 3}
        ])
        report = report_for(problem, self.assignment)
        report["totals"] = {"A": {"records": 999}}
        with self.assertRaises(ValidationError):
            split_indices(problem, report, self.y, self.groups)

    def test_rejects_valid_core_assignment_with_empty_partition_without_changing_problem(self):
        report = report_for(self.problem, {"g1": "A", "g2": "A", "g3": "A"})
        self.assertTrue(check_report(self.problem, report)["valid"])
        before = self.problem.digest
        with self.assertRaises(ValidationError):
            split_indices(self.problem, report, self.y, self.groups)
        self.assertEqual(self.problem.requirements, ())
        self.assertEqual(self.problem.digest, before)

    def test_rejects_empty_test_even_when_every_train_would_be_nonempty(self):
        report = report_for(self.problem, {"g1": "A", "g2": "B", "g3": "A"})
        self.assertTrue(check_report(self.problem, report)["valid"])
        with self.assertRaises(ValidationError):
            split_indices(self.problem, report, self.y, self.groups)

    def test_does_not_mutate_source_rows_or_report(self):
        before = copy.deepcopy((self.y, self.groups, self.report))
        split_indices(self.problem, self.report, self.y, self.groups)
        self.assertEqual((self.y, self.groups, self.report), before)

    def test_rejects_forged_problem_digest_even_if_report_matches_it(self):
        problem = replace(self.problem, digest="0" * 64)
        report = report_for(problem, self.assignment)
        with self.assertRaises(ValidationError):
            split_indices(problem, report, self.y, self.groups)


class ReportCrossValidatorTests(unittest.TestCase):
    setUp = SplitIndicesTests.setUp

    def test_protocol_yields_checked_splits_and_does_not_inspect_features(self):
        cv = ReportCrossValidator(self.problem, self.report)
        self.assertEqual(cv.get_n_splits(), 3)
        self.assertEqual(list(cv.split(object(), self.y, self.groups)),
                         split_indices(self.problem, self.report, self.y, self.groups))

    def test_requires_y_and_groups_for_every_split(self):
        cv = ReportCrossValidator(self.problem, self.report)
        for kwargs in ({}, {"y": self.y}, {"groups": self.groups}):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValidationError):
                    list(cv.split(**kwargs))

    def test_validates_report_before_exposing_a_validator(self):
        with self.assertRaises(ValidationError):
            ReportCrossValidator(self.problem, {**self.report, "status": "UNKNOWN"})

    def test_rejects_forged_problem_before_exposing_a_validator(self):
        problem = replace(self.problem, digest="0" * 64)
        with self.assertRaises(ValidationError):
            ReportCrossValidator(problem, report_for(problem, self.assignment))

    def test_snapshots_report_assignment_against_later_mutation(self):
        cv = ReportCrossValidator(self.problem, self.report)
        self.report["assignment"][0]["partition"] = "C"
        self.assertEqual(list(cv.split(y=self.y, groups=self.groups))[1][1], [1, 4])

    def test_checks_rows_again_on_every_split(self):
        cv = ReportCrossValidator(self.problem, self.report)
        list(cv.split(y=self.y, groups=self.groups))
        changed_y = ["a"] + self.y[1:]
        with self.assertRaises(ValidationError):
            list(cv.split(y=changed_y, groups=self.groups))

    def test_valid_core_empty_partition_is_rejected_on_split(self):
        report = report_for(self.problem, {"g1": "A", "g2": "A", "g3": "A"})
        cv = ReportCrossValidator(self.problem, report)
        with self.assertRaises(ValidationError):
            list(cv.split(y=self.y, groups=self.groups))

    def test_optional_protocol_has_no_sklearn_or_numpy_dependency(self):
        code = ("import sys; from foldcontract.adapter import ReportCrossValidator; "
                "assert 'sklearn' not in sys.modules; assert 'numpy' not in sys.modules")
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


class ExampleTests(unittest.TestCase):
    def test_all_published_examples_are_strict_core_problems(self):
        root = Path(__file__).resolve().parents[1]
        paths = sorted((root / "examples").glob("*.json"))
        self.assertGreaterEqual(len(paths), 3)
        for path in paths:
            with self.subTest(path=path.name):
                problem = parse_problem(path.read_bytes())
                self.assertTrue(problem.digest)

    def test_examples_have_their_documented_solver_statuses(self):
        from foldcontract.solver import solve

        root = Path(__file__).resolve().parents[1]
        expected = {"feasible-coverage.json": "FEASIBLE",
                    "infeasible-coverage.json": "INFEASIBLE",
                    "infeasible-coverage-size.json": "INFEASIBLE",
                    "unconstrained.json": "FEASIBLE"}
        for filename, status in expected.items():
            with self.subTest(filename=filename):
                problem = parse_problem((root / "examples" / filename).read_bytes())
                report = solve(problem)
                self.assertEqual(report["status"], status)
                if status == "FEASIBLE":
                    self.assertTrue(check_report(problem, report)["valid"])
                self.assertEqual(solve(problem, max_nodes=0)["status"], "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
