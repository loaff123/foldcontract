"""CLI status and atomic no-clobber publication tests."""
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

DATA = {"version": 1, "groups": ["g1", "g2"], "classes": ["c"], "partitions": ["a", "b"],
        "counts": [[1], [1]], "requirements": []}
ROOT = Path(__file__).resolve().parents[1]


class CLITests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)
        self.input = self.path / "input.json"
        self.input.write_text(json.dumps(DATA))

    def cli(self, *args):
        import foldcontract
        installed_root = Path(foldcontract.__file__).resolve().parents[1]
        env = dict(os.environ, PYTHONPATH=str(installed_root))
        return subprocess.run([sys.executable, "-m", "foldcontract", *map(str, args)],
                              capture_output=True, text=True, env=env, timeout=15)

    def test_solve_stdout_and_valid_exit(self):
        p = self.cli("solve", self.input)
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        self.assertEqual(json.loads(p.stdout)["status"], "FEASIBLE")

    def test_validate(self):
        p = self.cli("validate", self.input)
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        self.assertEqual(json.loads(p.stdout)["status"], "VALID")

    def test_infeasible_exit(self):
        obj = dict(DATA, requirements=[{"id": "x", "measure": "records", "partition": "a", "lower": 3}])
        self.input.write_text(json.dumps(obj))
        p = self.cli("solve", self.input)
        self.assertEqual(p.returncode, 2, p.stderr + p.stdout)
        self.assertEqual(json.loads(p.stdout)["status"], "INFEASIBLE")

    def test_unknown_exit(self):
        p = self.cli("solve", self.input, "--max-nodes", "0")
        self.assertEqual(p.returncode, 3, p.stderr + p.stdout)
        self.assertEqual(json.loads(p.stdout)["status"], "UNKNOWN")

    def test_validation_error_exit(self):
        self.input.write_text("invalid")
        p = self.cli("solve", self.input)
        self.assertEqual(p.returncode, 4, p.stderr + p.stdout)

    def test_output_file_and_no_clobber(self):
        out = self.path / "report.json"
        p = self.cli("solve", self.input, "--output", out)
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        original = out.read_bytes()
        self.assertEqual(json.loads(original)["status"], "FEASIBLE")
        p = self.cli("solve", self.input, "--output", out)
        self.assertEqual(p.returncode, 5, p.stderr + p.stdout)
        self.assertEqual(out.read_bytes(), original)

    def test_cannot_overwrite_input(self):
        original = self.input.read_bytes()
        p = self.cli("solve", self.input, "--output", self.input)
        self.assertEqual(p.returncode, 5, p.stderr + p.stdout)
        self.assertEqual(self.input.read_bytes(), original)

    def test_symlink_preserved_including_dangling(self):
        for target in [self.input, self.path / "missing"]:
            out = self.path / ("link-" + target.name)
            out.symlink_to(target)
            p = self.cli("solve", self.input, "--output", out)
            self.assertEqual(p.returncode, 5, p.stderr + p.stdout)
            self.assertTrue(out.is_symlink())
            self.assertEqual(os.readlink(out), str(target))

    def test_atomic_no_clobber_race(self):
        try:
            module = importlib.import_module("foldcontract.cli")
        except ModuleNotFoundError:
            self.fail("The atomic CLI writer is not implemented")
        out = self.path / "race.json"
        real_link = os.link
        def raced_link(src, dst, **kwargs):
            Path(dst).write_bytes(b"racer")
            return real_link(src, dst, **kwargs)
        with mock.patch.object(module.os, "link", side_effect=raced_link):
            with self.assertRaises(FileExistsError):
                module._atomic_write(out, b"replacement")
        self.assertEqual(out.read_bytes(), b"racer")
        self.assertEqual(sorted(p.name for p in self.path.iterdir()), ["input.json", "race.json"])

    def test_check_witness_and_reject_tampering(self):
        report = self.path / "report.json"
        p = self.cli("solve", self.input, "--output", report)
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        p = self.cli("check", self.input, report)
        self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
        self.assertTrue(json.loads(p.stdout)["valid"])
        r = json.loads(report.read_text())
        r["assignment"] = []
        report.write_text(json.dumps(r))
        p = self.cli("check", self.input, report)
        self.assertEqual(p.returncode, 4, p.stderr + p.stdout)
        self.assertFalse(json.loads(p.stdout)["valid"])

    def test_check_nonfeasible_assertion_not_proof(self):
        report = self.path / "report.json"
        report.write_text('{"status":"INFEASIBLE"}')
        p = self.cli("check", self.input, report)
        self.assertEqual(p.returncode, 4, p.stderr + p.stdout)

    def test_invalid_cli_arguments_are_validation_errors_not_infeasible(self):
        for args in [(), ("unknown",), ("solve", str(self.input), "--max-nodes", "nope")]:
            with self.subTest(args=args):
                p = self.cli(*args)
                self.assertEqual(p.returncode, 4, p.stderr + p.stdout)
                self.assertEqual(json.loads(p.stdout)["status"], "VALIDATION_ERROR")

    def test_missing_file_error(self):
        p = self.cli("solve", self.path / "absent")
        self.assertEqual(p.returncode, 5, p.stderr + p.stdout)
        self.assertEqual(json.loads(p.stdout)["status"], "ERROR")


if __name__ == "__main__":
    unittest.main()
