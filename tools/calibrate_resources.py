#!/usr/bin/env python3
"""Reproducible Linux resource-scope observations; no aggregate quota claim.

Run: PYTHONPATH=src python tools/calibrate_resources.py
Output: evidence/runtime-calibration.json
"""
import json
import os
from pathlib import Path
import platform
import resource
import subprocess
import sys
import threading
import time
import tracemalloc
from unittest import mock

from foldcontract import runtime

ROOT = Path(__file__).resolve().parents[1]


def name(prefix, i):
    text = prefix + f"{i:04d}-"
    return text + "x" * (128 - len(text))


def profile():
    groups = [name("g", i) for i in range(128)]
    classes = [name("c", i) for i in range(32)]
    partitions = [name("p", i) for i in range(8)]
    quotient, remainder = divmod(1_000_000_000, 128 * 32)
    counts = [[quotient + (g*32+c < remainder) for c in range(32)] for g in range(128)]
    requirements = [{"id": name("r", i), "partition": partitions[i % 8],
                     "measure": "class", "class": classes[(i // 8) % 32],
                     "lower": 0, "upper": 1_000_000_000} for i in range(1024)]
    return json.dumps(dict(version=1, groups=groups, classes=classes, partitions=partitions,
                           counts=counts, requirements=requirements), separators=(",", ":")).encode()


def sample_memory(pid):
    try:
        lines = Path(f"/proc/{pid}/status").read_text().splitlines()
    except FileNotFoundError:
        return {}
    return {line.split(":")[0]: int(line.split()[1]) * 1024 for line in lines
            if line.startswith(("VmSize:", "VmRSS:"))}


def observe(label, data, **options):
    processes = []
    maxima = {"VmSize": 0, "VmRSS": 0}
    stop = threading.Event()
    popen = subprocess.Popen
    def launch(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process
    def measure():
        while not stop.wait(0.002):
            for process in processes[:]:
                for key, value in sample_memory(process.pid).items():
                    maxima[key] = max(maxima[key], value)
    before = sample_memory(os.getpid())
    monitor = threading.Thread(target=measure)
    monitor.start()
    tracemalloc.start()
    started = time.monotonic()
    try:
        with mock.patch.object(runtime.subprocess, "Popen", side_effect=launch):
            result = runtime.run_bytes(data, **options)
        elapsed = time.monotonic() - started
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
        stop.set()
        monitor.join()
    return {
        "case": label, "input_bytes": len(data), "options": options,
        "elapsed_seconds": elapsed, "status": result["status"], "reason": result.get("reason"),
        "nodes": result.get("nodes"), "runtime": result.get("runtime"),
        "parent_baseline_observed_bytes": before,
        "parent_operation_python_allocation_peak_bytes": peak,
        "parent_cumulative_peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "worker_sampled_peak_bytes": maxima,
        "worker_sampling_interval_seconds": 0.002,
        "worker_processes_reaped": all(p.poll() is not None for p in processes),
        "worker_proc_entries_absent": all(not Path(f"/proc/{p.pid}").exists() for p in processes),
    }


def main():
    if sys.platform != "linux":
        raise SystemExit("This calibration uses Linux /proc and does not claim other-platform support")
    admitted = profile()
    result = {"python": platform.python_version(), "system": platform.system(), "release": platform.release(),
              "machine": platform.machine(), "profile": {"groups": 128, "classes": 32, "partitions": 8,
                  "requirements": 1024, "id_utf8_bytes": 128, "total_records": 1_000_000_000},
              "measurement_limits": ["Sampled worker peaks can miss short-lived peaks",
                  "Python allocation peak is operation-only, excluding preexisting caller state and input construction",
                  "Parent cumulative peak RSS includes earlier measurements and is not a per-operation delta",
                  "Measurement overhead is included; these are local observations, not performance guarantees",
                  "Only the worker address-space cap is enforced; parent and aggregate process-tree memory are uncapped"],
              "runs": [observe("maximum_admitted_validate", admitted, operation="validate"),
                       observe("maximum_admitted_tiny_node_solve", admitted, max_nodes=1),
                       observe("maximum_size_malformed", b" " * (runtime.INPUT_LIMIT - 1) + b"{")]}
    target = ROOT / "evidence" / "runtime-calibration.json"
    target.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps([{key: row[key] for key in ("case", "input_bytes", "elapsed_seconds", "status", "reason", "parent_operation_python_allocation_peak_bytes", "worker_sampled_peak_bytes", "worker_processes_reaped")} for row in result["runs"]], indent=2))


if __name__ == "__main__":
    main()
