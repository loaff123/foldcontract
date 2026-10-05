"""Contract-level admission tests, written before the model implementation."""
import copy
import dataclasses
import hashlib
import importlib
import importlib.util
import json
import unittest
import tracemalloc


def sample():
    return {"version": 1, "groups": ["g2", "g1"], "classes": ["z", "a"],
            "partitions": ["train", "test"], "counts": [[2, 1], [0, 3]],
            "requirements": [{"id": "coverage", "partition": "test", "measure": "class",
                              "class": "a", "lower": 1}]}


class ModelTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec("foldcontract.model"),
                             "The model admission API has not been implemented")
        self.model = importlib.import_module("foldcontract.model")

    def reject(self, value, code=None):
        with self.assertRaises(self.model.ValidationError) as caught:
            self.model.problem_from_dict(value)
        self.assertIsInstance(caught.exception.code, str)
        self.assertIsInstance(caught.exception.message, str)
        if code:
            self.assertEqual(caught.exception.code, code)

    def test_normalizes_names_and_permuted_matrix(self):
        p = self.model.problem_from_dict(sample())
        self.assertEqual(p.groups, ("g1", "g2"))
        self.assertEqual(p.classes, ("a", "z"))
        self.assertEqual(p.partitions, ("test", "train"))
        self.assertEqual(p.counts, ((3, 0), (1, 2)))
        self.assertEqual(p.requirements[0].upper, 4)
        self.assertEqual(p.requirements[0].class_name, "a")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            p.groups = ()

    def test_canonical_digest_and_roundtrip_are_stable(self):
        p = self.model.problem_from_dict(sample())
        canonical = self.model.problem_to_dict(p)
        expected = hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False,
                                             separators=(",", ":")).encode("utf-8")).hexdigest()
        self.assertEqual(p.digest, expected)
        self.assertEqual(self.model.parse_problem(json.dumps(canonical)), p)
        self.assertEqual(self.model.parse_problem(json.dumps(canonical).encode()), p)
        original = sample()
        original["groups"].reverse()
        original["counts"].reverse()
        original["classes"].reverse()
        original["counts"] = [list(reversed(row)) for row in original["counts"]]
        original["partitions"].reverse()
        self.assertEqual(self.model.problem_from_dict(original), p)

    def test_ids_are_exact_and_not_unicode_normalized(self):
        raw = sample()
        raw["groups"] = ["é", "e\u0301"]
        p = self.model.problem_from_dict(raw)
        self.assertEqual(p.groups, ("e\u0301", "é"))

    def test_repeated_constraints_remain_named_conjunction(self):
        raw = sample()
        raw["requirements"] += [{"id": "a-upper", "partition": "test", "measure": "class",
                                  "class": "a", "upper": 0}]
        p = self.model.problem_from_dict(raw)
        self.assertEqual(tuple(r.id for r in p.requirements), ("a-upper", "coverage"))
        self.assertEqual([(r.lower, r.upper) for r in p.requirements], [(0, 0), (1, 4)])
        reduced = self.model.with_requirements(p, ["coverage"])
        self.assertEqual([r.id for r in reduced.requirements], ["coverage"])
        self.assertNotEqual(reduced.digest, p.digest)
        self.assertEqual(self.model.with_requirements(p, [] ).requirements, ())
        with self.assertRaises(self.model.ValidationError):
            self.model.with_requirements(p, ["missing"])

    def test_bounds_outside_totals_and_inverted_are_admitted(self):
        raw = sample()
        raw["requirements"] = [{"id": "impossible", "partition": "train", "measure": "groups",
                                 "lower": 1_000_000_000, "upper": 0}]
        p = self.model.problem_from_dict(raw)
        self.assertEqual((p.requirements[0].lower, p.requirements[0].upper), (1_000_000_000, 0))

    def test_no_requirements_adds_no_hidden_constraints(self):
        raw = sample()
        raw["requirements"] = []
        self.assertEqual(self.model.problem_from_dict(raw).requirements, ())

    def test_default_bounds_match_measure_total(self):
        raw = sample()
        raw["requirements"] = [{"id": measure, "partition": "test", "measure": measure,
                                 "lower": 0} for measure in ("records", "groups")]
        p = self.model.problem_from_dict(raw)
        self.assertEqual({r.id: r.upper for r in p.requirements}, {"records": 6, "groups": 2})

    def test_unknown_and_missing_fields_are_rejected(self):
        for key in sample():
            raw = sample()
            del raw[key]
            with self.subTest(missing=key):
                self.reject(raw)
        raw = sample()
        raw["surprise"] = 0
        self.reject(raw)
        raw = sample()
        raw["requirements"][0]["surprise"] = 0
        self.reject(raw)
        raw = sample()
        del raw["requirements"][0]["lower"]
        self.reject(raw)

    def test_boolean_float_negative_and_overflow_counts_rejected(self):
        for bad in [True, False, 1.0, -1, 1_000_000_001, "1", None]:
            raw = sample()
            raw["counts"][0][0] = bad
            with self.subTest(value=bad):
                self.reject(raw)
        raw = sample()
        raw["counts"] = [[1_000_000_000, 1], [0, 3]]
        self.reject(raw)

    def test_boolean_float_negative_and_overflow_bounds_rejected(self):
        for key in ("lower", "upper"):
            for bad in [True, 1.0, -1, 1_000_000_001, "1", None]:
                raw = sample()
                raw["requirements"][0][key] = bad
                with self.subTest(key=key, value=bad):
                    self.reject(raw)

    def test_invalid_ids_and_duplicate_ids_are_rejected(self):
        for field in ("groups", "classes", "partitions"):
            for bad in ["", "x" * 129, "é" * 65, "\ud800", 1, True, None]:
                raw = sample()
                raw[field][0] = bad
                with self.subTest(field=field, value=repr(bad)):
                    self.reject(raw)
            raw = sample()
            raw[field][0] = raw[field][1]
            self.reject(raw)
        raw = sample()
        raw["requirements"].append(copy.deepcopy(raw["requirements"][0]))
        self.reject(raw)

    def test_maximum_id_byte_length_is_accepted(self):
        raw = sample()
        raw["groups"][0] = "é" * 64
        self.assertIn("é" * 64, self.model.problem_from_dict(raw).groups)

    def test_matrix_shape_and_zero_rows_columns_are_rejected(self):
        for counts in [[], [[1, 1]], [[1], [1]], [[0, 0], [1, 2]], [[0, 1], [0, 2]],
                       [[1, 1], [1, 1], [1, 1]], "invalid"]:
            raw = sample()
            raw["counts"] = counts
            with self.subTest(counts=counts):
                self.reject(raw)

    def test_dimension_limits_are_enforced(self):
        for field, count in [("groups", 0), ("groups", 129), ("classes", 0),
                             ("classes", 33), ("partitions", 1), ("partitions", 9)]:
            raw = sample()
            raw[field] = [f"x{i}" for i in range(count)]
            with self.subTest(field=field, count=count):
                self.reject(raw)
        raw = sample()
        raw["requirements"] = [{"id": f"r{i}", "partition": "test", "measure": "groups",
                                 "lower": 0} for i in range(1025)]
        self.reject(raw)

    def test_maximum_dimensions_and_requirements_are_admitted(self):
        raw = {"version": 1, "groups": [f"g{i}" for i in range(128)],
               "classes": [f"c{i}" for i in range(32)], "partitions": [f"p{i}" for i in range(8)],
               "counts": [[1] * 32 for _ in range(128)],
               "requirements": [{"id": f"r{i}", "partition": "p0", "measure": "groups",
                                  "lower": 0} for i in range(1024)]}
        self.assertEqual(len(self.model.problem_from_dict(raw).requirements), 1024)

    def test_measure_schema_and_references_are_strict(self):
        for change in [{"partition": "missing"}, {"class": "missing"}, {"measure": "unknown"},
                       {"measure": "groups"}, {"id": ""}]:
            raw = sample()
            raw["requirements"][0].update(change)
            self.reject(raw)
        raw = sample()
        del raw["requirements"][0]["class"]
        self.reject(raw)

    def test_version_and_top_level_types_are_strict(self):
        for version in [True, 1.0, 2, "1", None]:
            raw = sample()
            raw["version"] = version
            self.reject(raw)
        for value in [None, [], "{}", 1]:
            self.reject(value)

    def test_duplicate_json_keys_rejected_at_any_level(self):
        for text in ['{"version":1,"version":1}',
                     json.dumps(sample()).replace('"lower": 1', '"lower": 1, "lower": 0')]:
            with self.subTest(text=text), self.assertRaises(self.model.ValidationError) as caught:
                self.model.parse_problem(text)
            self.assertEqual(caught.exception.code, "DUPLICATE_KEY")

    def test_json_numbers_reject_nonfinite_and_floats(self):
        for value in ["NaN", "Infinity", "-Infinity", "1.0", "1e0"]:
            text = json.dumps(sample()).replace('"version": 1', '"version": ' + value)
            with self.subTest(value=value), self.assertRaises(self.model.ValidationError):
                self.model.parse_problem(text)

    def test_numeric_token_limit_precedes_integer_allocation(self):
        text = '{"version":' + '1' * 100_000 + '}'
        with self.assertRaises(self.model.ValidationError) as caught:
            self.model.parse_problem(text)
        self.assertEqual(caught.exception.code, "INTEGER_TOKEN")
        # Digits in strings are IDs, not numeric tokens.
        raw = sample()
        raw["groups"][0] = "1" * 128
        self.model.parse_problem(json.dumps(raw))

    def test_depth_limit_precedes_json_recursion(self):
        text = '[' * 100_000 + '0' + ']' * 100_000
        with self.assertRaises(self.model.ValidationError) as caught:
            self.model.parse_problem(text)
        self.assertEqual(caught.exception.code, "JSON_DEPTH")
        # Escaped braces inside strings do not contribute nesting.
        raw = sample()
        raw["groups"][0] = '"\\{{[[[[['
        self.model.parse_problem(json.dumps(raw))

    def test_input_size_utf8_and_syntax_are_strict(self):
        for value in [b"x" * (4 * 1024 * 1024 + 1), "é" * (2 * 1024 * 1024 + 1)]:
            with self.assertRaises(self.model.ValidationError) as caught:
                self.model.parse_problem(value)
            self.assertEqual(caught.exception.code, "INPUT_SIZE")
        for value in [b"\xff", "\ud800", "{} garbage", "", None, bytearray(b"{}")]:
            with self.subTest(value=repr(value)[:40]), self.assertRaises(self.model.ValidationError):
                self.model.parse_problem(value)

    def test_public_problem_revalidation_rejects_constructor_bypass(self):
        self.assertTrue(hasattr(self.model, "validated_problem"),
                        "Public Problem values need strict boundary revalidation")
        p = self.model.problem_from_dict(sample())
        self.assertEqual(self.model.validated_problem(p), p)
        malformed = [dataclasses.replace(p, counts=((-3, 0), (1, 2))),
                     dataclasses.replace(p, counts=((True, 0), (1, 2))),
                     dataclasses.replace(p, counts=((3.0, 0), (1, 2))),
                     dataclasses.replace(p, groups=tuple(reversed(p.groups))),
                     dataclasses.replace(p, groups=list(p.groups)),
                     dataclasses.replace(p, counts=(list(p.counts[0]), p.counts[1])),
                     dataclasses.replace(p, digest="0" * 64),
                     dataclasses.replace(p, requirements=(dataclasses.replace(p.requirements[0], lower=True),)),
                     dataclasses.replace(p, requirements=("not a requirement",)), None]
        for value in malformed:
            with self.subTest(value=value), self.assertRaises(self.model.ValidationError):
                self.model.validated_problem(value)

    def test_oversized_programmatic_object_rejected_before_key_copy(self):
        raw = {f"unexpected-{i}": i for i in range(100_000)}
        tracemalloc.start()
        try:
            self.reject(raw)
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertLess(peak, 128 * 1024, "Admission must reject excessive fields before copying keys")

    def test_public_serialization_and_subsets_reject_forged_problem(self):
        p = self.model.problem_from_dict(sample())
        forged = dataclasses.replace(p, digest="0" * 64)
        with self.assertRaises(self.model.ValidationError):
            self.model.problem_to_dict(forged)
        with self.assertRaises(self.model.ValidationError):
            self.model.with_requirements(forged, [])

    def test_subset_validates_problem_before_inspecting_requirements(self):
        p = self.model.problem_from_dict(sample())
        for forged in [None, dataclasses.replace(p, requirements=(None,))]:
            with self.subTest(forged=forged), self.assertRaises(self.model.ValidationError):
                self.model.with_requirements(forged, [])

    def test_requirement_selection_bounds_iterable_consumption(self):
        p = self.model.problem_from_dict(sample())
        consumed = []

        def excessive_ids():
            for index in range(self.model.MAX_REQUIREMENTS + 1):
                consumed.append(index)
                yield "coverage"
            self.fail("Selection read past its bounded overflow sentinel")

        with self.assertRaises(self.model.ValidationError) as caught:
            self.model.with_requirements(p, excessive_ids())
        self.assertEqual(caught.exception.code, "LIMIT_EXCEEDED")
        self.assertEqual(len(consumed), self.model.MAX_REQUIREMENTS + 1)
        accepted = self.model.with_requirements(p, ("coverage" for _ in range(self.model.MAX_REQUIREMENTS)))
        self.assertEqual(accepted, p)


if __name__ == "__main__":
    unittest.main()
