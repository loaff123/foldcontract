import importlib.util
import unittest
from test_solver import fixture, oracle


class ExplainTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('foldcontract.explain'), 'explanation feature missing')

    def problem(self):
        return fixture([[1], [1]], requirements=[
            dict(id='at-least-two', partition='p0', measure='records', lower=2),
            dict(id='at-most-one', partition='p0', measure='records', upper=1),
            dict(id='redundant', partition='p1', measure='groups', lower=0)])

    def test_named_core_independent_witnesses(self):
        from foldcontract.explain import explain
        from foldcontract.model import with_requirements
        from foldcontract.checker import check_assignment
        p = self.problem()
        result = explain(p)
        self.assertEqual(result['status'], 'INFEASIBLE')
        e = result['explanation']
        self.assertTrue(e['minimal'])
        self.assertTrue(e['complete'])
        self.assertEqual(set(e['requirement_ids']), {'at-least-two', 'at-most-one'})
        core = with_requirements(p, e['requirement_ids'])
        self.assertIsNone(oracle(core))
        self.assertEqual(len(e['removal_witnesses']), 2)
        for item in e['removal_witnesses']:
            smaller = with_requirements(core, set(e['requirement_ids']) - {item['removed']})
            self.assertTrue(check_assignment(smaller, item['assignment'])['valid'])

    def test_explanation_budget_keeps_original_infeasibility(self):
        from foldcontract.explain import explain
        result = explain(self.problem(), max_checks=0)
        self.assertEqual(result['status'], 'INFEASIBLE')
        self.assertFalse(result['explanation']['minimal'])
        self.assertFalse(result['explanation']['complete'])

    def test_shared_total_node_budget(self):
        from foldcontract.explain import explain
        result = explain(self.problem(), max_nodes=1)
        self.assertEqual(result['status'], 'INFEASIBLE')
        self.assertLessEqual(result['nodes'], 1)
        self.assertFalse(result['explanation']['minimal'])

    def test_original_unknown_and_feasible_have_no_infeasible_core(self):
        from foldcontract.explain import explain
        self.assertEqual(explain(self.problem(), max_nodes=0)['status'], 'UNKNOWN')
        self.assertEqual(explain(fixture([[1], [2]]))['status'], 'FEASIBLE')

    def test_bounded_witness_payload_preserves_original_result(self):
        from unittest.mock import patch
        from foldcontract.explain import explain
        with patch('foldcontract.explain.MAX_WITNESS_BYTES', 1, create=True):
            result = explain(self.problem())
        self.assertEqual(result['status'], 'INFEASIBLE')
        self.assertFalse(result['explanation']['minimal'])
        self.assertEqual(result['explanation']['reason'], 'explanation_output_limit')
