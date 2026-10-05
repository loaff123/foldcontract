"""Bounded, supervised Linux API around the deterministic node-only core.

The default 512 MiB limit is worker virtual address space, not caller RSS or an
aggregate process-tree limit. The fixed worker never launches descendants. Its
Python/stdlib bootstrap, this calling process, and final external publication
are outside that memory cap. Wall time is a parent-observed deadline, checked
before and after synchronous bounded work; it is not a real-time guarantee.
"""
import json
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import sys
import time

from . import __version__
from .worker import INPUT_LIMIT, OUTPUT_LIMIT, decode_bounded_json

STDERR_LIMIT = 64 * 1024


def _worker_command(operation, max_nodes, max_checks, deadline, memory_bytes, check_length):
    return [sys.executable, "-I", "-S", str(Path(__file__).with_name("worker.py").resolve()),
            operation, str(max_nodes), str(max_checks), repr(deadline), str(memory_bytes), str(check_length)]


def _stop_worker(process):
    # The child has a new session, so never signal the caller's process group.
    # This cleans cooperative descendants in that group, not arbitrary escaped
    # process trees. No user code or worker descendants are part of this API.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    finally:
        process.wait()


def _exchange(command, data, deadline):
    """Pump bounded pipes without communicate()'s unbounded output buffering."""
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, start_new_session=True, close_fds=True,
                               bufsize=0)
    output = bytearray()
    errors = bytearray()
    try:
        with selectors.DefaultSelector() as selector:
            for stream, kind, event in [(process.stdin, "input", selectors.EVENT_WRITE),
                                         (process.stdout, "output", selectors.EVENT_READ),
                                         (process.stderr, "stderr", selectors.EVENT_READ)]:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, event, kind)
            offset = 0
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None, "TIMEOUT", None
                for key, _ in selector.select(min(remaining, 0.05)):
                    stream = key.fileobj
                    if key.data == "input":
                        try:
                            count = os.write(stream.fileno(), memoryview(data)[offset:offset + 65536])
                            offset += count
                        except BrokenPipeError:
                            offset = len(data)
                        except BlockingIOError:
                            continue
                        if offset == len(data):
                            selector.unregister(stream)
                            stream.close()
                    else:
                        try:
                            chunk = os.read(stream.fileno(), 65536)
                        except BlockingIOError:
                            continue
                        if not chunk:
                            selector.unregister(stream)
                            stream.close()
                            continue
                        destination, cap = (output, OUTPUT_LIMIT) if key.data == "output" else (errors, STDERR_LIMIT)
                        if len(destination) + len(chunk) > cap:
                            return None, "OUTPUT_TOO_LARGE" if key.data == "output" else "STDERR_TOO_LARGE", None
                        destination.extend(chunk)
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None, "TIMEOUT", None
            try:
                exitcode = process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                return None, "TIMEOUT", None
            if exitcode != 0:
                return None, "WORKER_EXIT", exitcode
            return bytes(output), None, 0
    finally:
        _stop_worker(process)
        for stream in (process.stdin, process.stdout, process.stderr):
            stream.close()


def _limits(timeout, memory_mb, max_nodes, max_checks):
    return {
        "backend": "linux_rlimit_as" if sys.platform == "linux" else "unsupported",
        "memory": {"scope": "worker_address_space", "requested_mb": memory_mb,
                   "limit_bytes": memory_mb * 1024**2 if type(memory_mb) is int else None,
                   "enforced": False, "parent_enforced": False,
                   "aggregate_process_tree_enforced": False, "bootstrap_enforced": False},
        "wall_time": {"limit_seconds": timeout, "scope": "parent_observed_operation",
                      "absolute_deadline_guaranteed": False, "publication_included": False,
                      "deadline_exceeded": False, "completed_infeasibility_preserved": False,
                      "includes": ["worker_startup", "input_parsing", "normalization", "solve",
                                   "worker_serialization", "parent_protocol_decode", "parent_verification",
                                   "parent_serialization_check"],
                      "limitations": ["bounded_parent_steps_are_not_preempted", "cleanup_and_os_scheduling_may_overrun",
                                      "caller_input_creation_and_file_reading_excluded", "caller_serialization_excluded"]},
        "node_limit": max_nodes, "explanation_check_limit": max_checks,
        "input_limit_bytes": INPUT_LIMIT, "output_limit_bytes": OUTPUT_LIMIT,
        "stderr_limit_bytes": STDERR_LIMIT,
        "parent_verification": {"performed": False, "provenance_bound": False,
                                "infeasibility_independently_proven": False,
                                "explanation_witnesses_rechecked_in_parent": False, "explanation_schema_checked": False,
                                "memory_enforced": False,
                                "input_and_output_byte_bounds_enforced": True},
        "process_cleanup": "linux_worker_process_group; escaped_descendants_unsupported",
        "unsupported": ["parent_memory_limit", "aggregate_process_tree_memory_limit",
                        "interpreter_bootstrap_memory_limit", "absolute_publication_deadline"],
    }


def _valid_options(operation, max_nodes, timeout, memory_mb, max_checks):
    if type(operation) is not str or operation not in {"solve", "validate", "explain", "check"}:
        return "INVALID_OPERATION"
    if type(max_nodes) is not int or not 0 <= max_nodes <= 10_000_000:
        return "INVALID_NODE_LIMIT"
    if type(max_checks) is not int or not 0 <= max_checks <= 1024:
        return "INVALID_CHECK_LIMIT"
    if type(timeout) not in (float, int) or not 0 <= timeout <= 86400 or not math.isfinite(timeout):
        return "INVALID_TIMEOUT"
    if type(memory_mb) is not int or not 1 <= memory_mb <= sys.maxsize // 1024**2:
        return "INVALID_MEMORY_LIMIT"
    return None


def _valid_explanation(problem, explanation, max_checks):
    """Check bounded protocol shape, not reduction sufficiency or necessity.

    The fixed worker independently checks removal witnesses. The parent binds
    their names and assignment shapes without claiming to recheck every bound.
    """
    fields = {"requirement_ids", "minimal", "complete", "checks", "removal_witnesses",
              "reason", "relevant_groups", "class_totals"}
    if type(explanation) is not dict or len(explanation) != len(fields) or set(explanation) != fields:
        return False
    original = {requirement.id for requirement in problem.requirements}
    retained = explanation["requirement_ids"]
    if (type(retained) is not list or not 1 <= len(retained) <= len(original)
            or any(type(name) is not str or name not in original for name in retained)
            or len(set(retained)) != len(retained)):
        return False
    if (type(explanation["minimal"]) is not bool or type(explanation["complete"]) is not bool
            or explanation["minimal"] != explanation["complete"]):
        return False
    checks = explanation["checks"]
    if type(checks) is not int or not 0 <= checks <= min(max_checks, len(original)):
        return False
    reason = explanation["reason"]
    if type(reason) is not str:
        return False
    if explanation["minimal"]:
        if reason != "deletion_minimal_checked":
            return False
    elif reason not in {"timeout", "check_limit", "node_limit", "explanation_output_limit", "minimality_unproven"}:
        return False
    relevant = explanation["relevant_groups"]
    if type(relevant) is not list or relevant != list(problem.groups):
        return False
    totals = explanation["class_totals"]
    if type(totals) is not dict or len(totals) != len(problem.classes) or set(totals) != set(problem.classes):
        return False
    for index, name in enumerate(problem.classes):
        if type(totals[name]) is not int or totals[name] != sum(row[index] for row in problem.counts):
            return False
    witnesses = explanation["removal_witnesses"]
    if type(witnesses) is not list or len(witnesses) > min(checks, len(retained)):
        return False
    retained_names = set(retained)
    removed_names = set()
    groups = set(problem.groups)
    partitions = set(problem.partitions)
    for witness in witnesses:
        if type(witness) is not dict or len(witness) != 2 or set(witness) != {"removed", "assignment"}:
            return False
        removed = witness["removed"]
        if type(removed) is not str or removed not in retained_names or removed in removed_names:
            return False
        removed_names.add(removed)
        assignment = witness["assignment"]
        if type(assignment) is not list or len(assignment) != len(groups):
            return False
        seen = set()
        for entry in assignment:
            if type(entry) is not dict or len(entry) != 2 or set(entry) != {"group", "partition"}:
                return False
            group, partition = entry["group"], entry["partition"]
            if (type(group) is not str or type(partition) is not str or group not in groups
                    or partition not in partitions or group in seen):
                return False
            seen.add(group)
    return explanation["minimal"] == (removed_names == retained_names)


def _run(data, *, operation, max_nodes, timeout, memory_mb, max_checks, report_data=None):
    started = time.monotonic()
    options_error = _valid_options(operation, max_nodes, timeout, memory_mb, max_checks)
    # Never put unvalidated arbitrary objects/nonfinite numbers in JSON metadata.
    if options_error:
        return {"status": "VALIDATION_ERROR", "reason": options_error}
    metadata = _limits(timeout, memory_mb, max_nodes, max_checks)
    deadline = started + timeout

    def finish(report, observe_deadline=True):
        expired = time.monotonic() >= deadline
        metadata["wall_time"]["deadline_exceeded"] = expired
        preserve = (report.get("status") == "INFEASIBLE" and metadata["parent_verification"]["provenance_bound"])
        if observe_deadline and expired:
            if preserve:
                metadata["wall_time"]["completed_infeasibility_preserved"] = True
            else:
                report = {"status": "UNKNOWN", "reason": "TIMEOUT", "nodes": 0}
        result = dict(report, runtime=metadata)
        encoded = json.dumps(result, ensure_ascii=True, allow_nan=False, separators=(",", ":")).encode("ascii")
        if len(encoded) + 1 > OUTPUT_LIMIT:
            result = {"status": "ERROR", "reason": "OUTPUT_TOO_LARGE", "runtime": metadata}
        if observe_deadline and time.monotonic() >= deadline:
            metadata["wall_time"]["deadline_exceeded"] = True
            if preserve and result.get("status") == "INFEASIBLE":
                metadata["wall_time"]["completed_infeasibility_preserved"] = True
            elif result.get("status") != "ERROR":
                result = {"status": "UNKNOWN", "reason": "TIMEOUT", "nodes": 0, "runtime": metadata}
        return result

    if type(data) is not bytes or (report_data is not None and type(report_data) is not bytes):
        return finish({"status": "VALIDATION_ERROR", "reason": "INPUT_MUST_BE_BYTES"}, False)
    if len(data) > INPUT_LIMIT:
        return finish({"status": "VALIDATION_ERROR", "reason": "INPUT_TOO_LARGE"}, False)
    if report_data is not None and len(report_data) > OUTPUT_LIMIT:
        return finish({"status": "VALIDATION_ERROR", "reason": "REPORT_TOO_LARGE"}, False)
    if time.monotonic() >= deadline:
        return finish({"status": "UNKNOWN", "reason": "TIMEOUT", "nodes": 0})
    if sys.platform != "linux":
        return finish({"status": "ERROR", "reason": "UNSUPPORTED_MEMORY_BACKEND"})
    try:
        payload = data if report_data is None else data + report_data
        command = _worker_command(operation, max_nodes, max_checks, deadline,
                                  memory_mb * 1024**2, len(data) if report_data is not None else 0)
        raw, failure, exitcode = _exchange(command, payload, deadline)
        if failure:
            result = {"status": "UNKNOWN" if failure == "TIMEOUT" else "ERROR", "reason": failure}
            if exitcode is not None:
                result["worker_exitcode"] = exitcode
            return finish(result, failure == "TIMEOUT")
        try:
            envelope = decode_bounded_json(raw)
            if (type(envelope) is not dict or set(envelope) != {"protocol", "limits", "report"}
                    or type(envelope["protocol"]) is not int or envelope["protocol"] != 1):
                raise ValueError("Invalid protocol envelope")
            report, limits = envelope["report"], envelope["limits"]
            if type(limits) is not dict or type(report) is not dict:
                raise ValueError("Invalid protocol types")
            if (type(limits.get("memory_enforced")) is not bool
                    or type(limits.get("memory_limit_bytes")) is not int
                    or limits["memory_limit_bytes"] != memory_mb * 1024**2):
                raise ValueError("Invalid memory acknowledgement")
            allowed = {"ERROR", "UNKNOWN", "VALIDATION_ERROR"}
            allowed |= {"VALID"} if operation == "validate" else ({"CHECKED"} if operation == "check" else {"FEASIBLE", "INFEASIBLE"})
            if report.get("status") not in allowed:
                raise ValueError("Unexpected status")
            if not limits["memory_enforced"] and report.get("status") not in {"ERROR", "UNKNOWN"}:
                raise ValueError("Unenforced worker success")
            if "nodes" in report and (type(report["nodes"]) is not int or not 0 <= report["nodes"] <= max_nodes):
                raise ValueError("Invalid node accounting")
            if report.get("status") in {"FEASIBLE", "INFEASIBLE"}:
                if type(report.get("nodes")) is not int or not 0 < report["nodes"] <= max_nodes:
                    raise ValueError("Invalid node accounting")
                expected_reason = "checked_witness" if report["status"] == "FEASIBLE" else "exhausted_search"
                if report.get("engine_version") != __version__ or report.get("reason") != expected_reason:
                    raise ValueError("Invalid engine provenance")
            if report.get("status") in {"FEASIBLE", "INFEASIBLE", "VALID", "CHECKED"}:
                digest = report.get("problem_digest")
                if type(digest) is not str or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                    raise ValueError("Invalid problem digest")
            if report.get("status") == "VALID":
                for key, lower, upper in [("groups", 1, 128), ("classes", 1, 32), ("partitions", 2, 8), ("requirements", 0, 1024)]:
                    if type(report.get(key)) is not int or not lower <= report[key] <= upper:
                        raise ValueError("Invalid validation summary")
            if report.get("status") == "CHECKED" and type(report.get("valid")) is not bool:
                raise ValueError("Invalid check response")
            if report.get("status") == "UNKNOWN" and report.get("reason") not in {
                    "TIMEOUT", "MEMORY_LIMIT", "NODE_LIMIT", "INTERRUPTED", "node_limit", "timeout", "deadline"}:
                raise ValueError("Unrecognized resource exhaustion")
        except (ValueError, TypeError, UnicodeError, RecursionError):
            return finish({"status": "ERROR", "reason": "WORKER_PROTOCOL"}, False)
        metadata["memory"]["enforced"] = limits["memory_enforced"]
        baseline = limits.get("bootstrap_address_space_bytes")
        if type(baseline) is int and baseline >= 0:
            metadata["memory"]["bootstrap_address_space_bytes"] = baseline
        if report["status"] in {"FEASIBLE", "INFEASIBLE", "VALID", "CHECKED"}:
            from .model import parse_problem
            problem = parse_problem(data)
            if report["problem_digest"] != problem.digest:
                return finish({"status": "ERROR", "reason": "WORKER_PROTOCOL"}, False)
            metadata["parent_verification"]["provenance_bound"] = True
            if report["status"] == "VALID" and any(report[key] != len(getattr(problem, key)) for key in ("groups", "classes", "partitions", "requirements")):
                return finish({"status": "ERROR", "reason": "WORKER_PROTOCOL"}, False)
        if operation == "explain" and report["status"] == "INFEASIBLE":
            if not _valid_explanation(problem, report.get("explanation"), max_checks):
                return finish({"status": "ERROR", "reason": "WORKER_PROTOCOL"}, False)
            metadata["parent_verification"]["explanation_schema_checked"] = True
        if report["status"] == "FEASIBLE" or (report["status"] == "CHECKED" and report["valid"]):
            from .checker import check_report
            check = check_report(problem, report if operation != "check" else decode_bounded_json(report_data))
            metadata["parent_verification"]["performed"] = True
            if not check.get("valid"):
                return finish({"status": "ERROR", "reason": "INVALID_WORKER_WITNESS"}, False)
        return finish(report)
    except KeyboardInterrupt:
        return finish({"status": "UNKNOWN", "reason": "INTERRUPTED"}, False)
    except MemoryError:
        return finish({"status": "ERROR", "reason": "PARENT_MEMORY_ERROR"}, False)
    except Exception as exc:
        return finish({"status": "ERROR", "reason": "SUPERVISOR_FAILURE", "error_type": type(exc).__name__}, False)


def run_bytes(data: bytes, *, operation="solve", max_nodes=100000, timeout=10.0,
              memory_mb=512, max_checks=1024) -> dict:
    """Validate/solve/explain bytes with explicit scope metadata and observed limits."""
    if operation == "check":
        return {"status": "VALIDATION_ERROR", "reason": "USE_RUN_CHECK_BYTES"}
    return _run(data, operation=operation, max_nodes=max_nodes, timeout=timeout,
                memory_mb=memory_mb, max_checks=max_checks)


def run_check_bytes(data: bytes, report_data: bytes, *, timeout=10.0, memory_mb=512) -> dict:
    """Supervise existing FEASIBLE-witness checks without running the solver."""
    return _run(data, report_data=report_data, operation="check", max_nodes=0,
                timeout=timeout, memory_mb=memory_mb, max_checks=0)
