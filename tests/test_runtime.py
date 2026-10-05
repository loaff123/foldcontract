"""Supervised API, real-process watchdog, and hostile protocol regression tests."""
import importlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
DATA = json.dumps({"version": 1, "groups": ["g1", "g2"], "classes": ["c"],
    "partitions": ["a", "b"], "counts": [[1], [1]], "requirements": []}).encode()


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        try:
            self.runtime = importlib.import_module("foldcontract.runtime")
        except ModuleNotFoundError:
            self.fail("The supervised runtime API has not been implemented")

    def run_data(self, **kw):
        return self.runtime.run_bytes(DATA, **kw)

    def test_feasible_has_independent_check_and_honest_scopes(self):
        r = self.run_data()
        self.assertEqual(r["status"], "FEASIBLE", r)
        limits = r["runtime"]
        self.assertTrue(limits["parent_verification"]["performed"])
        self.assertEqual(limits["memory"]["scope"], "worker_address_space")
        self.assertEqual(limits["memory"]["limit_bytes"], 512 * 1024**2)
        self.assertTrue(limits["memory"]["enforced"])
        self.assertFalse(limits["memory"]["parent_enforced"])
        self.assertFalse(limits["memory"]["aggregate_process_tree_enforced"])
        self.assertFalse(limits["wall_time"]["absolute_deadline_guaranteed"])
        self.assertFalse(limits["wall_time"]["publication_included"])
        self.assertEqual(limits["node_limit"], 100000)

    def test_validate_uses_worker(self):
        r = self.run_data(operation="validate")
        self.assertEqual(r["status"], "VALID", r)
        self.assertTrue(r["runtime"]["memory"]["enforced"])

    def test_explain_infeasible(self):
        p = json.loads(DATA)
        p["requirements"] = [{"id": "too-many", "partition": "a", "measure": "records", "lower": 3}]
        r = self.runtime.run_bytes(json.dumps(p).encode(), operation="explain")
        self.assertEqual(r["status"], "INFEASIBLE", r)
        self.assertIn("explanation", r)

    def test_invalid_json_is_validation_error(self):
        for data in [b"{", b'{"version":1,"version":1}', b'NaN', b'\xff']:
            with self.subTest(data=data):
                self.assertEqual(self.runtime.run_bytes(data)["status"], "VALIDATION_ERROR")

    def test_admission_input_limit(self):
        r = self.runtime.run_bytes(b" " * (4 * 1024**2 + 1))
        self.assertEqual(r["status"], "VALIDATION_ERROR")
        self.assertEqual(r["reason"], "INPUT_TOO_LARGE")

    def test_bad_budget_types_rejected(self):
        for opts in [{"max_nodes": True}, {"max_nodes": -1}, {"timeout": float("nan")},
                     {"timeout": -1}, {"memory_mb": 0}, {"memory_mb": True},
                     {"max_checks": -1}, {"operation": "exec"}]:
            with self.subTest(opts=opts):
                self.assertEqual(self.run_data(**opts)["status"], "VALIDATION_ERROR")

    def test_zero_timeout_observed(self):
        r = self.run_data(timeout=0)
        self.assertEqual((r["status"], r["reason"]), ("UNKNOWN", "TIMEOUT"))

    def test_zero_node_budget(self):
        r = self.run_data(max_nodes=0)
        self.assertEqual(r["status"], "UNKNOWN", r)
        self.assertEqual(r["nodes"], 0)

    def test_unsupported_platform_is_not_claimed_enforced(self):
        with mock.patch.object(self.runtime.sys, "platform", "unsupported-test-os"):
            r = self.run_data()
        self.assertEqual((r["status"], r["reason"]), ("ERROR", "UNSUPPORTED_MEMORY_BACKEND"))
        self.assertFalse(r["runtime"]["memory"]["enforced"])

    def fake_worker(self, source, **kw):
        with tempfile.TemporaryDirectory() as td:
            script = Path(td) / "fault.py"
            script.write_text(source)
            with mock.patch.object(self.runtime, "_worker_command", return_value=[sys.executable, "-I", "-S", str(script)]):
                return self.run_data(**kw)

    def test_child_exit_is_error_not_resource_unknown(self):
        r = self.fake_worker("raise SystemExit(42)")
        self.assertEqual((r["status"], r["reason"]), ("ERROR", "WORKER_EXIT"))

    def test_malformed_protocol_is_error(self):
        for source in ["print('not json')", "print('{}')", "print('{\"protocol\":1,\"protocol\":1}')"]:
            with self.subTest(source=source):
                r = self.fake_worker(source)
                self.assertEqual(r["status"], "ERROR")

    def test_output_limit_is_error_not_resource_success(self):
        r = self.fake_worker("import os\nos.write(1, b'x' * (8 * 1024**2 + 1))")
        self.assertEqual((r["status"], r["reason"]), ("ERROR", "OUTPUT_TOO_LARGE"))

    def test_stderr_is_bounded(self):
        r = self.fake_worker("import os\nos.write(2, b'x' * (64 * 1024 + 1))")
        self.assertEqual((r["status"], r["reason"]), ("ERROR", "STDERR_TOO_LARGE"))

    def test_hung_worker_observed_timeout(self):
        started = time.monotonic()
        r = self.fake_worker("import time\ntime.sleep(30)", timeout=0.1)
        self.assertEqual((r["status"], r["reason"]), ("UNKNOWN", "TIMEOUT"))
        self.assertLess(time.monotonic() - started, 5)

    def test_os_kill_is_not_guessed_as_memory_exhaustion(self):
        r = self.fake_worker("import os,signal\nos.kill(os.getpid(), signal.SIGKILL)")
        self.assertEqual((r["status"], r["reason"]), ("ERROR", "WORKER_EXIT"))

    def test_actual_worker_memory_shortage(self):
        r = self.run_data(memory_mb=1)
        self.assertEqual((r["status"], r["reason"]), ("UNKNOWN", "MEMORY_LIMIT"))

    def test_parent_independently_rejects_false_witness(self):
        from foldcontract.model import parse_problem
        digest = parse_problem(DATA).digest
        envelope = {"protocol": 1, "limits": {"memory_enforced": True, "memory_limit_bytes": 512*1024**2},
                    "report": {"status": "FEASIBLE", "problem_digest": digest, "assignment": [], "nodes": 1, "engine_version": "0.1.0a1", "reason": "checked_witness"}}
        r = self.fake_worker("print(" + repr(json.dumps(envelope)) + ")")
        self.assertEqual((r["status"], r["reason"]), ("ERROR", "INVALID_WORKER_WITNESS"))

    def test_runtime_does_not_accept_memory_limit_ack_mismatch(self):
        envelope = {"protocol": 1, "limits": {"memory_enforced": True, "memory_limit_bytes": 1},
                    "report": {"status": "UNKNOWN", "reason": "NODE_LIMIT", "nodes": 0}}
        r = self.fake_worker("print(" + repr(json.dumps(envelope)) + ")")
        self.assertEqual((r["status"], r["reason"]), ("ERROR", "WORKER_PROTOCOL"))

    def test_decided_results_require_bound_provenance(self):
        from foldcontract.model import parse_problem
        good = {"status": "INFEASIBLE", "nodes": 1, "problem_digest": parse_problem(DATA).digest,
                "engine_version": "0.1.0a1", "reason": "exhausted_search"}
        changes = [{"problem_digest": "0" * 64}, {"problem_digest": "x"},
                   {"engine_version": "wrong"}, {"reason": "made-up"}]
        for change in changes:
            with self.subTest(change=change):
                envelope = {"protocol": 1, "limits": {"memory_enforced": True,
                            "memory_limit_bytes": 512*1024**2}, "report": dict(good, **change)}
                r = self.fake_worker("print(" + repr(json.dumps(envelope)) + ")")
                self.assertEqual((r["status"], r["reason"]), ("ERROR", "WORKER_PROTOCOL"))

    def test_options_match_core_admission_caps(self):
        for options in [{"max_nodes": 10000001}, {"max_checks": 1025}]:
            r = self.run_data(operation="validate", **options)
            self.assertEqual(r["status"], "VALIDATION_ERROR")

    def test_long_float_token_rejected_before_json_decoder(self):
        from foldcontract.worker import decode_bounded_json
        with mock.patch("foldcontract.worker.json.loads", side_effect=AssertionError("decoder called")):
            with self.assertRaises(ValueError):
                decode_bounded_json(b"0." + b"1" * 10000)

    def test_lexically_long_integer_rejected_before_json_decoder(self):
        from foldcontract.worker import decode_bounded_json
        with mock.patch("foldcontract.worker.json.loads", side_effect=AssertionError("decoder called")):
            with self.assertRaises(ValueError):
                decode_bounded_json(b"9" * 10000)

    def test_supervised_check_rejects_long_numeric_report_tokens(self):
        # Metadata beyond what the witness checker uses is legal, provided its
        # JSON admission bounds hold; oversized numeric tokens are rejected.
        r = self.runtime.run_check_bytes(DATA, b'{"status":"FEASIBLE","extra":0.' + b"1"*10000+b"}")
        self.assertEqual(r["status"], "VALIDATION_ERROR")

    def test_validation_summary_must_be_complete_and_match_input(self):
        from foldcontract.model import parse_problem
        report = {"status": "VALID", "problem_digest": parse_problem(DATA).digest}
        for fields in [{}, {"groups": 1, "classes": 1, "partitions": 2, "requirements": 0}]:
            envelope = {"protocol": 1, "limits": {"memory_enforced": True,
                        "memory_limit_bytes": 512*1024**2}, "report": dict(report, **fields)}
            r = self.fake_worker("print(" + repr(json.dumps(envelope)) + ")", operation="validate")
            self.assertEqual((r["status"], r["reason"]), ("ERROR", "WORKER_PROTOCOL"))

    def test_unknown_node_accounting_cannot_exceed_budget(self):
        envelope = {"protocol": 1, "limits": {"memory_enforced": True, "memory_limit_bytes": 512*1024**2},
                    "report": {"status": "UNKNOWN", "reason": "node_limit", "nodes": 100001}}
        r = self.fake_worker("print(" + repr(json.dumps(envelope)) + ")")
        self.assertEqual((r["status"], r["reason"]), ("ERROR", "WORKER_PROTOCOL"))

    def test_serialization_bound_reserves_cli_newline(self):
        report = {"status": "ERROR", "reason": "TEST_FAILURE", "message": ""}
        metadata = self.runtime._limits(10.0, 512, 100000, 1024)
        metadata["memory"]["enforced"] = True
        size = len(json.dumps(dict(report, runtime=metadata), separators=(",", ":")).encode())
        report["message"] = "x" * (self.runtime.OUTPUT_LIMIT - size)
        raw = json.dumps({"protocol": 1, "limits": {"memory_enforced": True,
                         "memory_limit_bytes": 512*1024**2}, "report": report}).encode()
        with mock.patch.object(self.runtime, "_exchange", return_value=(raw, None, 0)):
            result = self.run_data()
        self.assertLessEqual(len(json.dumps(result, separators=(",", ":")).encode()) + 1, self.runtime.OUTPUT_LIMIT)

    def test_explanation_schema_rejects_malformed_nested_values(self):
        import copy
        from foldcontract.model import parse_problem
        from foldcontract.explain import explain
        problem = json.loads(DATA)
        problem["requirements"] = [{"id": "too-many", "partition": "a", "measure": "groups", "lower": 3}]
        data = json.dumps(problem).encode()
        good = explain(parse_problem(data))
        changes = [
            {"requirement_ids": ["too-many", "too-many"]},
            {"requirement_ids": [False]}, {"minimal": 1}, {"complete": False},
            {"checks": True}, {"checks": 1025}, {"reason": "made-up"}, {"reason": []},
            {"relevant_groups": ["unknown"]}, {"class_totals": {"c": True}},
            {"removal_witnesses": "invalid"},
            {"removal_witnesses": [{"removed": "too-many", "assignment": []}]},
            {"removal_witnesses": [{"removed": "unknown", "assignment": good["explanation"]["removal_witnesses"][0]["assignment"]}]},
            {"removal_witnesses": [{"removed": "too-many", "assignment": [{"group": "g1", "partition": "a"}]*2}]},
            {"removal_witnesses": [{"removed": "too-many", "assignment": [{"group": "g1", "partition": "bad"}, {"group": "g2", "partition": "a"}]}]},
            {"removal_witnesses": good["explanation"]["removal_witnesses"] * 2},
            {"extra": "unexpected"},
        ]
        for changed in changes:
            with self.subTest(changed=changed):
                report = copy.deepcopy(good)
                report["explanation"].update(changed)
                raw = json.dumps({"protocol": 1, "limits": {"memory_enforced": True,
                                 "memory_limit_bytes": 512*1024**2}, "report": report}).encode()
                with mock.patch.object(self.runtime, "_exchange", return_value=(raw, None, 0)):
                    result = self.runtime.run_bytes(data, operation="explain")
                self.assertEqual((result["status"], result["reason"]), ("ERROR", "WORKER_PROTOCOL"))

    def test_invalid_nonhashable_operation_and_huge_timeout(self):
        for opts in [{"operation": []}, {"timeout": 10**10000}]:
            with self.subTest(option=next(iter(opts))):
                self.assertEqual(self.run_data(**opts)["status"], "VALIDATION_ERROR")

    def test_protocol_depth_and_integer_tokens_are_bounded(self):
        for text in ["[" * 10000 + "0" + "]" * 10000, "9" * 10000]:
            r = self.fake_worker("print(" + repr(text) + ")")
            self.assertEqual((r["status"], r["reason"]), ("ERROR", "WORKER_PROTOCOL"))

    def test_observed_timeout_includes_parent_verification(self):
        from foldcontract import checker
        actual = checker.check_report
        def slow_check(*args, **kwargs):
            time.sleep(0.3)
            return actual(*args, **kwargs)
        with mock.patch.object(checker, "check_report", side_effect=slow_check):
            r = self.run_data(timeout=0.2)
        self.assertEqual((r["status"], r["reason"]), ("UNKNOWN", "TIMEOUT"))
        self.assertTrue(r["runtime"]["parent_verification"]["performed"])

    def test_supervised_check_rejects_report_size_and_bad_json(self):
        for raw in [b"x", b"[" * 1000, b" " * (8 * 1024**2 + 1)]:
            r = self.runtime.run_check_bytes(DATA, raw)
            self.assertEqual(r["status"], "VALIDATION_ERROR", r)

    @unittest.skipUnless(sys.platform == "linux", "Only Linux enforcement supported")
    def test_real_rlimit_blocks_allocation_after_bootstrap(self):
        # Exercise the real worker's limit installation and MemoryError fallback,
        # replacing only its workload in this separate test-owned interpreter.
        source = ("import sys\n"
                  f"sys.path.insert(0, {str(Path(self.runtime.__file__).resolve().parents[1])!r})\n"
                  "from foldcontract import worker\n"
                  "def allocate(*args):\n"
                  "    return bytearray(64 * 1024**2)\n"
                  "worker._execute = allocate\n"
                  "worker.main()\n")
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "allocation.py"
            path.write_text(source)
            proc = subprocess.run([sys.executable, "-I", "-S", str(path), "solve", "1", "1",
                                   str(time.monotonic()+5), str(48*1024**2), "0"],
                                  capture_output=True, timeout=8)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        r = json.loads(proc.stdout)
        self.assertEqual((r["report"]["status"], r["report"]["reason"]), ("UNKNOWN", "MEMORY_LIMIT"))
        self.assertTrue(r["limits"]["memory_enforced"])
        self.assertEqual(r["limits"]["memory_limit_bytes"], 48 * 1024**2)
        self.assertLess(r["limits"]["bootstrap_address_space_bytes"], 48 * 1024**2)

    @unittest.skipUnless(sys.platform == "linux", "Linux process-state check")
    def test_timeout_cleanup_kills_worker_process_group(self):
        with tempfile.TemporaryDirectory() as td:
            pidfile = Path(td) / "pid"
            source = ("import subprocess,sys,time\n"
                      "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'])\n"
                      f"open({str(pidfile)!r},'w').write(str(p.pid))\n"
                      "time.sleep(30)\n")
            r = self.fake_worker(source, timeout=0.4)
            self.assertEqual(r["reason"], "TIMEOUT")
            self.assertTrue(pidfile.exists(), "Worker never reached descendant creation")
            pid = int(pidfile.read_text())
            for _ in range(100):
                stat = Path(f"/proc/{pid}/stat")
                if not stat.exists() or stat.read_text().split()[2] == "Z":
                    break
                time.sleep(0.01)
            else:
                os.kill(pid, signal.SIGKILL)
                self.fail("Worker descendant survived watchdog cleanup")


if __name__ == "__main__":
    unittest.main()
