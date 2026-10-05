"""Independent exact witness checking, with no solver or propagation dependency.

Totals are accumulated directly from admitted group/class counts. Any totals or
other derived claims supplied by an engine report are deliberately ignored.
"""
from .model import MAX_GROUPS, MAX_ID_BYTES, Problem, validated_problem


def _bounded_identifier(value: object) -> bool:
    """Keep malformed witness values out of diagnostics and serialized output."""
    if type(value) is not str or not 1 <= len(value) <= MAX_ID_BYTES:
        return False
    try:
        return len(value.encode("utf-8")) <= MAX_ID_BYTES
    except UnicodeEncodeError:
        return False


def check_assignment(problem: Problem, assignment: list[dict] | dict[str, str]) -> dict:
    """Check complete raw assignments and every original named requirement."""
    validated_problem(problem)
    violations = []
    totals = {partition: {"records": 0, "groups": 0,
                          "classes": {name: 0 for name in problem.classes}}
              for partition in problem.partitions}
    group_rows = {name: row for name, row in zip(problem.groups, problem.counts)}
    if type(assignment) in (dict, list) and len(assignment) > MAX_GROUPS:
        return {"valid": False, "violations": [{"code": "ASSIGNMENT_SIZE", "message": "Assignment exceeds the 128-group admission limit"}],
                "totals": totals}
    if type(assignment) is dict:
        entries = [{"group": group, "partition": partition} for group, partition in assignment.items()]
    elif type(assignment) is list:
        entries = assignment
    else:
        return {"valid": False, "violations": [{"code": "INVALID_ASSIGNMENT", "message": "Assignment must be an object or array"}],
                "totals": totals}
    seen = set()
    for index, entry in enumerate(entries):
        if type(entry) is not dict or len(entry) != 2 or set(entry) != {"group", "partition"}:
            violations.append({"code": "INVALID_ENTRY", "index": index})
            continue
        group = entry["group"]
        partition = entry["partition"]
        if not _bounded_identifier(group) or not _bounded_identifier(partition):
            violations.append({"code": "INVALID_ENTRY", "index": index})
            continue
        if group not in group_rows:
            violations.append({"code": "UNKNOWN_GROUP", "group": group})
            continue
        if group in seen:
            violations.append({"code": "DUPLICATE_GROUP", "group": group})
            continue
        seen.add(group)
        if partition not in totals:
            violations.append({"code": "UNKNOWN_PARTITION", "group": group, "partition": partition})
            continue
        aggregate = totals[partition]
        aggregate["groups"] += 1
        for class_name, count in zip(problem.classes, group_rows[group]):
            aggregate["classes"][class_name] += count
            aggregate["records"] += count
    for group in problem.groups:
        if group not in seen:
            violations.append({"code": "MISSING_GROUP", "group": group})
    for requirement in problem.requirements:
        partition_totals = totals[requirement.partition]
        if requirement.measure == "class":
            observed = partition_totals["classes"][requirement.class_name]
        elif requirement.measure == "records":
            observed = partition_totals["records"]
        else:
            observed = partition_totals["groups"]
        if observed < requirement.lower or observed > requirement.upper:
            violations.append({"code": "REQUIREMENT_VIOLATION", "requirement_id": requirement.id,
                               "partition": requirement.partition, "measure": requirement.measure,
                               "class": requirement.class_name, "lower": requirement.lower,
                               "upper": requirement.upper, "observed": observed})
    return {"valid": not violations, "violations": violations, "totals": totals}


def check_report(problem: Problem, report: dict) -> dict:
    """Validate only FEASIBLE reports bound to this exact canonical problem."""
    validated_problem(problem)
    violations = []
    if type(report) is not dict:
        violations.append({"code": "INVALID_REPORT", "message": "Report must be an object"})
    else:
        if report.get("status") != "FEASIBLE":
            violations.append({"code": "UNVERIFIABLE_STATUS", "message": "Only FEASIBLE witnesses can be checked"})
        if type(report.get("problem_digest")) is not str or report["problem_digest"] != problem.digest:
            violations.append({"code": "DIGEST_MISMATCH", "message": "Report does not match the canonical problem"})
        if "assignment" not in report:
            violations.append({"code": "MISSING_ASSIGNMENT"})
    if violations:
        return {"valid": False, "violations": violations, "totals": {}}
    return check_assignment(problem, report["assignment"])
