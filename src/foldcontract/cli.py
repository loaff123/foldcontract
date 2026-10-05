"""JSON CLI with atomic, no-clobber result publication."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile

from .runtime import INPUT_LIMIT, OUTPUT_LIMIT, run_bytes, run_check_bytes


def _read_bounded(path, cap):
    with open(path, "rb") as source:
        result = source.read(cap + 1)
    if len(result) > cap:
        raise ValueError("File exceeds byte limit")
    return result


def _atomic_write(path, data):
    """Publish an inode with an atomic hard-link create, never replace a target.

    A pre-existing file, symlink (even dangling), directory, or racing publisher
    wins. There is no exists-then-replace window. Unsupported hard links fail
    closed. Publication and filesystem I/O have no absolute deadline promise.
    """
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix=".foldcontract-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as target:
            target.write(data)
            target.flush()
            os.fsync(target.fileno())
        os.link(temporary, path)
    finally:
        os.unlink(temporary)


def _exit_code(report):
    if report.get("status") == "CHECKED":
        return 0 if report.get("valid") is True else 4
    return {"FEASIBLE": 0, "VALID": 0, "INFEASIBLE": 2, "UNKNOWN": 3,
            "VALIDATION_ERROR": 4, "ERROR": 5}.get(report.get("status"), 5)


class _CLIArgumentError(ValueError):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise _CLIArgumentError(message)


def _parser():
    parser = _ArgumentParser(prog="foldcontract", description="Bounded explicit group partition contracts")
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("validate", "solve", "explain", "check"):
        child = sub.add_parser(command)
        child.add_argument("input", help="Problem JSON file (maximum 4 MiB)")
        if command == "check":
            child.add_argument("report", help="FEASIBLE report JSON file (maximum 8 MiB)")
        else:
            child.add_argument("--max-nodes", type=int, default=100000)
            if command == "explain":
                child.add_argument("--max-checks", type=int, default=1024)
        child.add_argument("--timeout", type=float, default=10.0,
                           help="Observed operation seconds; excludes file I/O/publication (default: 10)")
        child.add_argument("--memory-mb", type=int, default=512,
                           help="Linux worker address-space MiB; excludes parent/bootstrap (default: 512)")
        child.add_argument("--output", help="New output file; existing paths are never replaced")
    return parser


def main(argv=None):
    try:
        args = _parser().parse_args(argv)
    except _CLIArgumentError as exc:
        result = {"status": "VALIDATION_ERROR", "reason": "INVALID_ARGUMENTS", "message": str(exc)}
        sys.stdout.write(json.dumps(result, sort_keys=True) + "\n")
        return 4
    try:
        data = _read_bounded(args.input, INPUT_LIMIT)
        if args.command == "check":
            result = run_check_bytes(data, _read_bounded(args.report, OUTPUT_LIMIT),
                                     timeout=args.timeout, memory_mb=args.memory_mb)
        else:
            result = run_bytes(data, operation=args.command, max_nodes=args.max_nodes,
                               max_checks=getattr(args, "max_checks", 1024), timeout=args.timeout,
                               memory_mb=args.memory_mb)
    except ValueError as exc:
        result = {"status": "VALIDATION_ERROR", "reason": "INPUT_TOO_LARGE", "message": str(exc)}
    except OSError as exc:
        result = {"status": "ERROR", "reason": "INPUT_IO_ERROR", "error_type": type(exc).__name__}
    raw = (json.dumps(result, ensure_ascii=True, sort_keys=True, allow_nan=False, separators=(",", ":")) + "\n").encode("ascii")
    try:
        if args.output:
            _atomic_write(args.output, raw)
        else:
            sys.stdout.buffer.write(raw)
            sys.stdout.buffer.flush()
    except OSError as exc:
        error = {"status": "ERROR", "reason": "OUTPUT_IO_ERROR", "error_type": type(exc).__name__}
        sys.stderr.write(json.dumps(error, sort_keys=True) + "\n")
        return 5
    return _exit_code(result)
