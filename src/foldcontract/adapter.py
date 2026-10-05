"""Explicit single-label row aggregation and checked index conversion.

Only labels and group IDs are read. No feature values, estimator scores, or
hidden balance objectives participate in constructing a problem or a split.
"""

from collections.abc import Sequence

from .checker import check_report
from .model import (
    MAX_CLASSES,
    MAX_GROUPS,
    MAX_ID_BYTES,
    MAX_PARTITIONS,
    MAX_RECORDS,
    MAX_REQUIREMENTS,
    Problem,
    Requirement,
    ValidationError,
    problem_from_dict,
    validated_problem,
)

__all__ = ["aggregate_rows", "kfold_problem", "split_indices", "ReportCrossValidator"]

# The aggregate core can describe more records than it is sensible to
# materialize as Python row-index lists. This is a separate adapter limit.
_MAX_ROWS = 1_000_000


def _sequence(value, name, maximum):
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray, memoryview)):
        raise ValidationError("ADAPTER_SEQUENCE", f"{name} must be a finite non-text sequence")
    length = len(value)
    if length > maximum:
        raise ValidationError("ADAPTER_LIMIT", f"{name} exceeds the {maximum} item adapter limit")
    return length


def _row_id(value, name):
    if not isinstance(value, str) or not value or len(value) > MAX_ID_BYTES:
        raise ValidationError("ADAPTER_ID", f"{name} must be a nonempty string of at most {MAX_ID_BYTES} UTF-8 bytes")
    try:
        encoded_length = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValidationError("ADAPTER_ID", f"{name} must be valid UTF-8") from exc
    if encoded_length > MAX_ID_BYTES:
        raise ValidationError("ADAPTER_ID", f"{name} exceeds {MAX_ID_BYTES} UTF-8 bytes")
    return value


def _row_counts(y, groups):
    row_count = _sequence(y, "y", _MAX_ROWS)
    group_rows = _sequence(groups, "groups", _MAX_ROWS)
    if row_count != group_rows:
        raise ValidationError("ADAPTER_LENGTH", "y and groups must have the same length")
    if row_count == 0:
        raise ValidationError("ADAPTER_EMPTY", "y and groups must contain at least one row")
    labels = set()
    counts_by_group = {}
    for index in range(row_count):
        label = _row_id(y[index], f"y[{index}]")
        group = _row_id(groups[index], f"groups[{index}]")
        labels.add(label)
        if len(labels) > MAX_CLASSES:
            raise ValidationError("ADAPTER_LIMIT", f"at most {MAX_CLASSES} classes are supported")
        if group not in counts_by_group:
            if len(counts_by_group) >= MAX_GROUPS:
                raise ValidationError("ADAPTER_LIMIT", f"at most {MAX_GROUPS} groups are supported")
            counts_by_group[group] = {}
        row = counts_by_group[group]
        row[label] = row.get(label, 0) + 1
    classes = tuple(sorted(labels))
    group_names = tuple(sorted(counts_by_group))
    counts = tuple(tuple(counts_by_group[group].get(label, 0) for label in classes)
                   for group in group_names)
    return group_names, classes, counts


def _requirement_dict(requirement):
    if not isinstance(requirement, Requirement):
        return requirement
    result = {
        "id": requirement.id,
        "partition": requirement.partition,
        "measure": requirement.measure,
        "lower": requirement.lower,
        "upper": requirement.upper,
    }
    if requirement.measure == "class" or requirement.class_name is not None:
        result["class"] = requirement.class_name
    return result


def aggregate_rows(y, groups, partitions, requirements=()) -> Problem:
    """Build a core problem from explicit finite sequences of string IDs.

    Rows are single-label and unweighted. Up to 1,000,000 rows are admitted;
    the core's group/class/partition/requirement limits also apply. Named
    requirements may be schema dictionaries or ``Requirement`` objects.
    No requirement is added implicitly, and inputs are not modified.
    """
    group_names, classes, counts = _row_counts(y, groups)
    _sequence(partitions, "partitions", MAX_PARTITIONS)
    _sequence(requirements, "requirements", MAX_REQUIREMENTS)
    return problem_from_dict({
        "version": 1,
        "groups": list(group_names),
        "classes": list(classes),
        "partitions": list(partitions),
        "counts": [list(row) for row in counts],
        "requirements": [_requirement_dict(req) for req in requirements],
    })


def kfold_problem(y, groups, n_splits=5, min_class_count=1) -> Problem:
    """Explicitly select the K-fold coverage preset.

    Creates ``fold_0`` through ``fold_{n_splits-1}``, one named minimum of
    one group per partition, and a named ``min_class_count`` lower bound
    for every class in every partition. This constructs input only; it
    does not solve, relax infeasible bounds, or balance partition sizes.
    """
    if type(n_splits) is not int or not 2 <= n_splits <= MAX_PARTITIONS:
        raise ValidationError("ADAPTER_N_SPLITS", f"n_splits must be an integer from 2 to {MAX_PARTITIONS}")
    if type(min_class_count) is not int or not 0 <= min_class_count <= MAX_RECORDS:
        raise ValidationError("ADAPTER_MINIMUM", f"min_class_count must be an integer from 0 to {MAX_RECORDS}")
    partitions = [f"fold_{index}" for index in range(n_splits)]
    problem = aggregate_rows(y, groups, partitions)
    requirements = []
    for partition in problem.partitions:
        requirements.append({"id": f"{partition}:groups:min", "partition": partition,
                             "measure": "groups", "lower": 1})
        for index, label in enumerate(problem.classes):
            # Ordinal IDs keep maximum-length user class names valid.
            requirements.append({"id": f"{partition}:class_{index}:min", "partition": partition,
                                 "measure": "class", "class": label, "lower": min_class_count})
    return problem_from_dict({
        "version": 1,
        "groups": list(problem.groups),
        "classes": list(problem.classes),
        "partitions": list(problem.partitions),
        "counts": [list(row) for row in problem.counts],
        "requirements": requirements,
    })


def _checked_assignment(problem, report):
    checked = check_report(problem, report)
    if not checked["valid"]:
        raise ValidationError("ADAPTER_REPORT", "report must contain a valid FEASIBLE witness with the matching problem digest")
    assignment = report["assignment"]
    if isinstance(assignment, dict):
        return dict(assignment)
    return {entry["group"]: entry["partition"] for entry in assignment}


def split_indices(problem, report, y, groups) -> list[tuple[list[int], list[int]]]:
    """Return checked (train, test) lists in canonical partition order.

    Each list preserves the supplied source row order. Source group/class
    counts must match exactly, even when rows have been reordered. The
    digest and witness are independently checked. Every test partition and
    its complementary train set must be nonempty; no new requirement is
    inserted to make that true. Time and returned space are O(k * n).
    """
    problem = validated_problem(problem)
    group_names, classes, counts = _row_counts(y, groups)
    if (group_names, classes, counts) != (problem.groups, problem.classes, problem.counts):
        raise ValidationError("ADAPTER_SOURCE_MISMATCH", "source rows do not match the problem's exact group/class counts")
    assignment = _checked_assignment(problem, report)
    row_count = len(y)
    # Reuse the same immutable integers across folds instead of allocating
    # a fresh Python integer for each train-list entry.
    row_indices = list(range(row_count))
    test_by_partition = {partition: [] for partition in problem.partitions}
    for index in row_indices:
        test_by_partition[assignment[groups[index]]].append(index)
    if any(not test or len(test) == row_count for test in test_by_partition.values()):
        raise ValidationError("ADAPTER_EMPTY_SPLIT", "every partition must have nonempty test and complementary train rows")
    splits = []
    for partition in problem.partitions:
        train = [index for index in row_indices if assignment[groups[index]] != partition]
        splits.append((train, test_by_partition[partition]))
    return splits


class ReportCrossValidator:
    """A dependency-free, explicit scikit-learn cross-validator protocol.

    A supplied core FEASIBLE report is checked and snapshotted. ``split``
    requires both ``y`` and ``groups`` each time and revalidates their exact
    counts. ``X`` is accepted only for protocol compatibility and is never
    inspected. This class neither fits estimators nor solves a problem.
    """

    def __init__(self, problem, report):
        problem = validated_problem(problem)
        assignment = _checked_assignment(problem, report)
        self._problem = problem
        self._report = {
            "status": "FEASIBLE",
            "problem_digest": problem.digest,
            "assignment": assignment,
        }

    def get_n_splits(self, X=None, y=None, groups=None):
        """Return the number of named partitions without inspecting rows."""
        return len(self._problem.partitions)

    def split(self, X=None, y=None, groups=None):
        """Yield checked train/test lists; explicitly supply y and groups."""
        return iter(split_indices(self._problem, self._report, y, groups))
