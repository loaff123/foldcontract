"""Strict, bounded JSON admission and canonical immutable problem data.

No input token is converted to an integer until the byte, lexical-depth and
numeric-token limits have been checked. Identifiers are exact Unicode strings;
no case folding, whitespace trimming or Unicode normalization is performed.
"""
from dataclasses import dataclass
import hashlib
import json
from typing import Iterable

MAX_INPUT_BYTES = 4 * 1024 * 1024
MAX_DEPTH = 6
MAX_INTEGER_DIGITS = 10
MAX_GROUPS = 128
MAX_CLASSES = 32
MAX_PARTITIONS = 8
MAX_REQUIREMENTS = 1024
MAX_ID_BYTES = 128
MAX_RECORDS = 1_000_000_000


class ValidationError(ValueError):
    """A stable machine-readable code plus a human-readable description."""

    def __init__(self, code: str, message: str | None = None):
        self.code = code
        self.message = code if message is None else message
        super().__init__(self.message)


@dataclass(frozen=True)
class Requirement:
    id: str
    partition: str
    measure: str
    class_name: str | None
    lower: int
    upper: int


@dataclass(frozen=True)
class Problem:
    groups: tuple[str, ...]
    classes: tuple[str, ...]
    partitions: tuple[str, ...]
    counts: tuple[tuple[int, ...], ...]
    requirements: tuple[Requirement, ...]
    digest: str


def _lexical_preflight(text: str) -> None:
    """Bound nesting and digit counts with constant auxiliary space.

    This is deliberately not a second JSON parser: the stdlib parser validates
    token grammar after this nonrecursive resource-admission pass.
    """
    depth = 0
    in_string = False
    escaped = False
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > MAX_DEPTH:
                raise ValidationError("JSON_DEPTH", "JSON nesting exceeds six containers")
        elif char in "]}":
            depth -= 1
        elif char == "-" or "0" <= char <= "9":
            digits = 0
            while index < length and text[index] in "0123456789eE+-.":
                if "0" <= text[index] <= "9":
                    digits += 1
                    if digits > MAX_INTEGER_DIGITS:
                        raise ValidationError("INTEGER_TOKEN", "A numeric token exceeds ten decimal digits")
                index += 1
            continue
        index += 1


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValidationError("DUPLICATE_KEY", "Duplicate JSON object key")
        value[key] = item
    return value


def _parse_integer(token: str) -> int:
    # Defense in depth if this callback is ever reused without the preflight.
    if len(token.lstrip("-")) > MAX_INTEGER_DIGITS:
        raise ValidationError("INTEGER_TOKEN", "An integer token exceeds ten decimal digits")
    return int(token)


def _reject_number(token: str):
    raise ValidationError("INVALID_NUMBER", "Only finite integer JSON numbers are accepted")


def parse_problem(data: bytes | str) -> Problem:
    """Admit UTF-8 JSON without unbounded recursion or integer allocation."""
    if type(data) is bytes:
        if len(data) > MAX_INPUT_BYTES:
            raise ValidationError("INPUT_SIZE", "Input exceeds four MiB")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError("INVALID_UTF8", "Input is not valid UTF-8") from exc
    elif type(data) is str:
        if len(data) > MAX_INPUT_BYTES:
            raise ValidationError("INPUT_SIZE", "Input exceeds four MiB")
        try:
            size = len(data.encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise ValidationError("INVALID_UTF8", "Input contains an invalid Unicode scalar") from exc
        if size > MAX_INPUT_BYTES:
            raise ValidationError("INPUT_SIZE", "Input exceeds four MiB")
        text = data
    else:
        raise ValidationError("INVALID_TYPE", "Input must be bytes or a string")
    _lexical_preflight(text)
    try:
        value = json.loads(text, object_pairs_hook=_unique_object, parse_int=_parse_integer,
                           parse_float=_reject_number, parse_constant=_reject_number)
    except json.JSONDecodeError as exc:
        raise ValidationError("INVALID_JSON", f"Invalid JSON at line {exc.lineno}, column {exc.colno}") from exc
    return problem_from_dict(value)


def _fields(value: object, required: set[str], optional: set[str], label: str) -> dict:
    if type(value) is not dict:
        raise ValidationError("INVALID_TYPE", f"{label} must be an object")
    if not len(required) <= len(value) <= len(required) + len(optional):
        raise ValidationError("INVALID_SCHEMA", f"{label} has missing or unknown fields")
    actual = set(value)
    if not required <= actual or not actual <= required | optional:
        raise ValidationError("INVALID_SCHEMA", f"{label} has missing or unknown fields")
    return value


def _identifier(value: object, label: str) -> str:
    if type(value) is not str or not value or len(value) > MAX_ID_BYTES:
        raise ValidationError("INVALID_ID", f"{label} must be a nonempty string of at most 128 UTF-8 bytes")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ValidationError("INVALID_ID", f"{label} contains an invalid Unicode scalar") from exc
    if size > MAX_ID_BYTES:
        raise ValidationError("INVALID_ID", f"{label} exceeds 128 UTF-8 bytes")
    return value


def _names(value: object, minimum: int, maximum: int, label: str) -> tuple[str, ...]:
    if type(value) is not list:
        raise ValidationError("INVALID_TYPE", f"{label} must be an array")
    if not minimum <= len(value) <= maximum:
        raise ValidationError("LIMIT_EXCEEDED", f"{label} requires between {minimum} and {maximum} entries")
    result = tuple(_identifier(item, label) for item in value)
    if len(set(result)) != len(result):
        raise ValidationError("DUPLICATE_ID", f"{label} contains duplicate identifiers")
    return result


def _integer(value: object, label: str, code: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_RECORDS:
        raise ValidationError(code, f"{label} must be an integer between zero and one billion")
    return value


def problem_from_dict(value: dict) -> Problem:
    """Validate every field, preserve named conjunctions and canonicalize order."""
    raw = _fields(value, {"version", "groups", "classes", "partitions", "counts", "requirements"},
                  set(), "Problem")
    if type(raw["version"]) is not int or raw["version"] != 1:
        raise ValidationError("INVALID_VERSION", "Only integer schema version 1 is supported")
    groups = _names(raw["groups"], 1, MAX_GROUPS, "groups")
    classes = _names(raw["classes"], 1, MAX_CLASSES, "classes")
    partitions = _names(raw["partitions"], 2, MAX_PARTITIONS, "partitions")
    matrix = raw["counts"]
    if type(matrix) is not list or len(matrix) != len(groups):
        raise ValidationError("INVALID_MATRIX", "counts must have one row per group")
    counts = []
    class_totals = [0] * len(classes)
    total = 0
    for row in matrix:
        if type(row) is not list or len(row) != len(classes):
            raise ValidationError("INVALID_MATRIX", "Each counts row must have one entry per class")
        checked_row = tuple(_integer(item, "Count", "INVALID_COUNT") for item in row)
        row_total = sum(checked_row)
        if row_total == 0:
            raise ValidationError("EMPTY_GROUP", "Every group must contain at least one record")
        total += row_total
        if total > MAX_RECORDS:
            raise ValidationError("LIMIT_EXCEEDED", "Total records exceeds one billion")
        for j, count in enumerate(checked_row):
            class_totals[j] += count
        counts.append(checked_row)
    if any(count == 0 for count in class_totals):
        raise ValidationError("EMPTY_CLASS", "Every listed class must occur")
    requirements = raw["requirements"]
    if type(requirements) is not list:
        raise ValidationError("INVALID_TYPE", "requirements must be an array")
    if len(requirements) > MAX_REQUIREMENTS:
        raise ValidationError("LIMIT_EXCEEDED", "At most 1024 requirements are admitted")
    class_by_name = dict(zip(classes, class_totals))
    partition_names = set(partitions)
    seen_requirements = set()
    admitted = []
    for item in requirements:
        item = _fields(item, {"id", "partition", "measure"}, {"class", "lower", "upper"}, "Requirement")
        name = _identifier(item["id"], "Requirement ID")
        if name in seen_requirements:
            raise ValidationError("DUPLICATE_ID", "Requirement IDs must be unique")
        seen_requirements.add(name)
        partition = _identifier(item["partition"], "Requirement partition")
        if partition not in partition_names:
            raise ValidationError("UNKNOWN_REFERENCE", "Requirement references an unknown partition")
        measure = item["measure"]
        if type(measure) is not str or measure not in ("class", "records", "groups"):
            raise ValidationError("INVALID_MEASURE", "Requirement measure must be class, records or groups")
        if "lower" not in item and "upper" not in item:
            raise ValidationError("INVALID_SCHEMA", "A requirement needs a lower or upper bound")
        class_name = None
        if measure == "class":
            if "class" not in item:
                raise ValidationError("INVALID_SCHEMA", "A class requirement needs a class identifier")
            class_name = _identifier(item["class"], "Requirement class")
            if class_name not in class_by_name:
                raise ValidationError("UNKNOWN_REFERENCE", "Requirement references an unknown class")
            available = class_by_name[class_name]
        else:
            if "class" in item:
                raise ValidationError("INVALID_SCHEMA", "Only class requirements may specify class")
            available = total if measure == "records" else len(groups)
        lower = _integer(item.get("lower", 0), "Lower bound", "INVALID_BOUND")
        upper = _integer(item.get("upper", available), "Upper bound", "INVALID_BOUND")
        admitted.append(Requirement(name, partition, measure, class_name, lower, upper))
    group_order = sorted(range(len(groups)), key=groups.__getitem__)
    class_order = sorted(range(len(classes)), key=classes.__getitem__)
    problem = Problem(tuple(groups[i] for i in group_order), tuple(classes[j] for j in class_order),
                      tuple(sorted(partitions)),
                      tuple(tuple(counts[i][j] for j in class_order) for i in group_order),
                      tuple(sorted(admitted, key=lambda r: r.id)), "")
    canonical = json.dumps(_problem_to_dict_unchecked(problem), ensure_ascii=False, sort_keys=True,
                           separators=(",", ":")).encode("utf-8")
    return Problem(problem.groups, problem.classes, problem.partitions, problem.counts,
                   problem.requirements, hashlib.sha256(canonical).hexdigest())


def _problem_to_dict_unchecked(problem: Problem) -> dict:
    """Serialize fields after an admission boundary has established their shape."""
    requirements = []
    for requirement in problem.requirements:
        item = {"id": requirement.id, "partition": requirement.partition,
                "measure": requirement.measure, "lower": requirement.lower, "upper": requirement.upper}
        if requirement.measure == "class":
            item["class"] = requirement.class_name
        requirements.append(item)
    return {"version": 1, "groups": list(problem.groups), "classes": list(problem.classes),
            "partitions": list(problem.partitions), "counts": [list(row) for row in problem.counts],
            "requirements": requirements}


def with_requirements(problem: Problem, ids: Iterable[str]) -> Problem:
    """Rebuild a problem containing exactly the selected named requirements."""
    validated_problem(problem)
    if isinstance(ids, (str, bytes)):
        raise ValidationError("INVALID_TYPE", "Requirement IDs must be an iterable of identifiers")
    try:
        iterator = iter(ids)
    except TypeError as exc:
        raise ValidationError("INVALID_TYPE", "Requirement IDs must be iterable") from exc
    selected = set()
    known = {requirement.id for requirement in problem.requirements}
    for index, name in enumerate(iterator):
        if index >= MAX_REQUIREMENTS:
            raise ValidationError("LIMIT_EXCEEDED", "At most 1024 requirement selections are admitted")
        if type(name) is not str or name not in known:
            raise ValidationError("UNKNOWN_REFERENCE", "Selected requirement ID is unknown")
        selected.add(name)
    raw = problem_to_dict(problem)
    raw["requirements"] = [item for item in raw["requirements"] if item["id"] in selected]
    return problem_from_dict(raw)


def validated_problem(problem: Problem) -> Problem:
    """Validate a public Problem, including immutable layout and digest.

    Dataclass constructors remain convenient value constructors, not an admission
    bypass: public operations call this boundary before trusting their fields.
    A canonical re-admission also verifies all named Requirement values.
    """
    if type(problem) is not Problem:
        raise ValidationError("INVALID_PROBLEM", "Expected a canonical Problem value")
    dimensions = ((problem.groups, 1, MAX_GROUPS), (problem.classes, 1, MAX_CLASSES),
                  (problem.partitions, 2, MAX_PARTITIONS),
                  (problem.requirements, 0, MAX_REQUIREMENTS))
    for field, minimum, maximum in dimensions:
        if type(field) is not tuple or not minimum <= len(field) <= maximum:
            raise ValidationError("INVALID_PROBLEM", "Problem dimensions must be bounded immutable tuples")
    if type(problem.counts) is not tuple or len(problem.counts) != len(problem.groups):
        raise ValidationError("INVALID_PROBLEM", "Problem counts must be an immutable matrix")
    for row in problem.counts:
        if type(row) is not tuple or len(row) != len(problem.classes):
            raise ValidationError("INVALID_PROBLEM", "Problem counts rows must be immutable and rectangular")
    if any(type(requirement) is not Requirement for requirement in problem.requirements):
        raise ValidationError("INVALID_PROBLEM", "Problem requirements must be Requirement values")
    if type(problem.digest) is not str or len(problem.digest) != 64:
        raise ValidationError("INVALID_PROBLEM", "Problem digest must be a canonical SHA-256 digest")
    canonical = problem_from_dict(_problem_to_dict_unchecked(problem))
    if canonical != problem:
        raise ValidationError("INVALID_PROBLEM", "Problem ordering, requirement fields or digest is not canonical")
    return problem


def problem_to_dict(problem: Problem) -> dict:
    """Return the validated canonical schema with explicit default bounds."""
    validated_problem(problem)
    return _problem_to_dict_unchecked(problem)
