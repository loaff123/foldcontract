"""Fixed isolated worker entry point. No user modules, callables, or pickle.

Run as a file with Python -I -S. The address-space limit is installed before
package imports, input parsing, normalization, search and serialization. Python
and this small stdlib bootstrap have already started at that point.
"""
import json
import math
import os
from pathlib import Path
import sys
import time

INPUT_LIMIT = 4 * 1024**2
OUTPUT_LIMIT = 8 * 1024**2


def decode_bounded_json(data, max_bytes=OUTPUT_LIMIT, max_depth=16):
    """Bound tokens/depth before decoding; reject duplicate keys and nonfinite JSON."""
    if len(data) > max_bytes:
        raise ValueError("JSON byte limit exceeded")
    depth = 0
    quoted = False
    escaped = False
    numeric_length = 0
    numeric_float = False
    numeric_digits = 0
    for byte in data:
        if quoted:
            if escaped:
                escaped = False
            elif byte == 92:
                escaped = True
            elif byte == 34:
                quoted = False
        elif byte in b"0123456789eE+.-":
            numeric_length += 1
            numeric_digits += 48 <= byte <= 57
            numeric_float = numeric_float or byte in b".eE"
            if numeric_length > 64:
                raise ValueError("JSON numeric token limit exceeded")
        elif byte == 34:
            if numeric_digits > 20 and not numeric_float:
                raise ValueError("JSON integer token limit exceeded")
            numeric_length = 0
            numeric_digits = 0
            numeric_float = False
            quoted = True
        elif byte in (91, 123):
            if numeric_digits > 20 and not numeric_float:
                raise ValueError("JSON integer token limit exceeded")
            numeric_length = 0
            numeric_digits = 0
            numeric_float = False
            depth += 1
            if depth > max_depth:
                raise ValueError("JSON nesting limit exceeded")
        elif byte in (93, 125):
            if numeric_digits > 20 and not numeric_float:
                raise ValueError("JSON integer token limit exceeded")
            numeric_length = 0
            numeric_digits = 0
            numeric_float = False
            depth -= 1
            if depth < 0:
                raise ValueError("Unbalanced JSON")
        else:
            if numeric_digits > 20 and not numeric_float:
                raise ValueError("JSON integer token limit exceeded")
            numeric_length = 0
            numeric_digits = 0
            numeric_float = False
    if numeric_digits > 20 and not numeric_float:
        raise ValueError("JSON integer token limit exceeded")

    def integer(token):
        if len(token.lstrip("-")) > 20:
            raise ValueError("JSON integer token limit exceeded")
        return int(token)

    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def number(token):
        result = float(token)
        if not math.isfinite(result):
            raise ValueError("Nonfinite JSON number")
        return result

    def constant(_):
        raise ValueError("Nonfinite JSON number")

    return json.loads(data.decode("utf-8"), parse_int=integer, parse_float=number,
                      parse_constant=constant, object_pairs_hook=pairs)


def _encode(report, limits):
    raw = json.dumps({"protocol": 1, "limits": limits, "report": report},
                     ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")
    if len(raw) > OUTPUT_LIMIT:
        raise OverflowError("Worker output limit exceeded")
    return raw


def _write(raw):
    view = memoryview(raw)
    while view:
        n = os.write(1, view)
        view = view[n:]


def _execute(operation, max_nodes, max_checks, deadline, check_input_length):
    from foldcontract.model import ValidationError, parse_problem
    try:
        if operation == "check":
            data = sys.stdin.buffer.read(check_input_length)
            if len(data) != check_input_length:
                raise ValueError("Truncated check input")
            report_data = sys.stdin.buffer.read(OUTPUT_LIMIT + 1)
        else:
            data = sys.stdin.buffer.read(INPUT_LIMIT + 1)
        problem = parse_problem(data)
        if time.monotonic() >= deadline:
            return {"status": "UNKNOWN", "reason": "TIMEOUT", "nodes": 0}
        if operation == "validate":
            return {"status": "VALID", "problem_digest": problem.digest,
                    "groups": len(problem.groups), "classes": len(problem.classes),
                    "partitions": len(problem.partitions), "requirements": len(problem.requirements)}
        if operation == "check":
            from foldcontract.checker import check_report
            try:
                report = decode_bounded_json(report_data)
            except (ValueError, UnicodeError, RecursionError) as exc:
                return {"status": "VALIDATION_ERROR", "reason": "INVALID_REPORT", "message": str(exc)}
            return dict(check_report(problem, report), status="CHECKED", problem_digest=problem.digest)
        if operation == "solve":
            from foldcontract.solver import solve
            return solve(problem, max_nodes=max_nodes, deadline=deadline)
        from foldcontract.explain import explain
        return explain(problem, max_nodes=max_nodes, max_checks=max_checks, deadline=deadline)
    except ValidationError as exc:
        return {"status": "VALIDATION_ERROR", "reason": getattr(exc, "code", "INVALID_INPUT"),
                "message": str(exc)}


def main():
    # All arguments are fixed operation names or validated numeric values owned by
    # the supervisor; none name executable code, callbacks, imports, or files.
    operation, nodes, checks, deadline, memory, check_length = sys.argv[1:]
    limits = {"memory_enforced": False, "memory_limit_bytes": int(memory)}
    memory_error = _encode({"status": "UNKNOWN", "reason": "MEMORY_LIMIT", "nodes": 0}, limits)
    try:
        import resource
        if sys.platform != "linux" or not hasattr(resource, "RLIMIT_AS"):
            _write(_encode({"status": "ERROR", "reason": "UNSUPPORTED_MEMORY_BACKEND"}, limits))
            return
        # Linux /proc supplies an observed bootstrap baseline. RLIMIT_AS limits
        # future mappings, so do not pretend a too-small cap shrank this baseline.
        with open("/proc/self/statm", "r", encoding="ascii") as handle:
            baseline = int(handle.read().split()[0]) * os.sysconf("SC_PAGE_SIZE")
        limits["bootstrap_address_space_bytes"] = baseline
        if baseline >= int(memory):
            _write(_encode({"status": "UNKNOWN", "reason": "MEMORY_LIMIT", "nodes": 0}, limits))
            return
        resource.setrlimit(resource.RLIMIT_AS, (int(memory), int(memory)))
        if resource.getrlimit(resource.RLIMIT_AS) != (int(memory), int(memory)):
            raise RuntimeError("RLIMIT_AS acknowledgement mismatch")
        limits["memory_enforced"] = True
        # Pre-encode the allocation-failure response while the cap is in effect.
        memory_error = _encode({"status": "UNKNOWN", "reason": "MEMORY_LIMIT", "nodes": 0}, limits)
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        report = _execute(operation, int(nodes), int(checks), float(deadline), int(check_length))
        _write(_encode(report, limits))
    except MemoryError:
        _write(memory_error)
    except OverflowError:
        _write(_encode({"status": "ERROR", "reason": "OUTPUT_TOO_LARGE"}, limits))
    except Exception as exc:
        _write(_encode({"status": "ERROR", "reason": "WORKER_FAILURE",
                        "error_type": type(exc).__name__}, limits))


if __name__ == "__main__":
    main()
