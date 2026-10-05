import importlib.util
import itertools
import random
import unittest


def fixture(counts, k=2, requirements=None):
    from foldcontract.model import problem_from_dict
    return problem_from_dict({
        'version': 1, 'groups': [f'g{i}' for i in range(len(counts))],
        'classes': [f'c{i}' for i in range(len(counts[0]))],
        'partitions': [f'p{i}' for i in range(k)], 'counts': counts,
        'requirements': requirements or [],
    })


def oracle(problem):
    for assignment in itertools.product(problem.partitions, repeat=len(problem.groups)):
        good = True
        for r in problem.requirements:
            value = 0
            for row, partition in zip(problem.counts, assignment):
                if partition != r.partition:
                    continue
                value += (sum(row) if r.measure == 'records' else 1 if r.measure == 'groups' else row[problem.classes.index(r.class_name)])
            if not r.lower <= value <= r.upper:
                good = False
                break
        if good:
            return dict(zip(problem.groups, assignment))
    return None


class SolverTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('foldcontract.solver'), 'solver feature missing')

    def test_empty_requirements_and_zero_budget(self):
        from foldcontract.solver import solve
        p = fixture([[1], [2]])
        self.assertEqual(solve(p, max_nodes=0)['status'], 'UNKNOWN')
        result = solve(p)
        self.assertEqual(result['status'], 'FEASIBLE')
        self.assertEqual(len(result['assignment']), 2)

    def test_odd_cycle_is_infeasible(self):
        from foldcontract.solver import solve
        reqs = [{'id': f'{p}-{c}', 'partition': f'p{p}', 'measure': 'class', 'class': f'c{c}', 'lower': 1} for p in range(2) for c in range(3)]
        p = fixture([[1, 1, 0], [1, 0, 1], [0, 1, 1]], requirements=reqs)
        self.assertEqual(solve(p)['status'], 'INFEASIBLE')

    def test_repeated_bounds_are_conjunction(self):
        from foldcontract.solver import solve
        reqs = [dict(id='minimum', partition='p0', measure='records', lower=2), dict(id='maximum', partition='p0', measure='records', upper=1)]
        self.assertEqual(solve(fixture([[1], [2]], requirements=reqs))['status'], 'INFEASIBLE')

    def test_integer_extremes_and_forced_upper_exclusion(self):
        from foldcontract.solver import solve
        reqs = [dict(id='full', partition='p0', measure='records', lower=999999999), dict(id='one', partition='p1', measure='records', lower=1, upper=1)]
        self.assertEqual(solve(fixture([[999999999], [1]], requirements=reqs))['status'], 'FEASIBLE')

    def test_deadline_and_invalid_budgets(self):
        from foldcontract.solver import solve
        from foldcontract.model import ValidationError
        p = fixture([[1]])
        self.assertEqual(solve(p, deadline=0)['reason'], 'timeout')
        for bad in [-1, True, 1.0, '1']:
            with self.assertRaises(ValidationError):
                solve(p, max_nodes=bad)
        for bad in (True, float('inf'), float('nan'), 10**1000, 'soon'):
            with self.assertRaises(ValidationError):
                solve(p, deadline=bad)

    def test_fixed_budget_determinism(self):
        from foldcontract.solver import solve
        p = fixture([[2, 0], [0, 1], [1, 1]], requirements=[dict(id='one', partition='p1', measure='groups', lower=1)])
        self.assertEqual(solve(p, max_nodes=10), solve(p, max_nodes=10))

    def test_320_small_cases_against_cartesian_enumeration(self):
        from foldcontract.solver import solve
        from foldcontract.checker import check_assignment
        rng = random.Random(42011)
        for case in range(320):
            n, c, k = rng.randint(1, 7), rng.randint(1, 3), rng.randint(2, 3)
            counts = [[rng.randint(0, 3) for _ in range(c)] for _ in range(n)]
            for row in counts:
                if not sum(row): row[0] = 1
            for col in range(c):
                if not sum(row[col] for row in counts): counts[0][col] = 1
            reqs = []
            for j in range(rng.randint(0, 12)):
                measure = rng.choice(['class', 'records', 'groups'])
                r = dict(id=f'r{j}', partition=f'p{rng.randrange(k)}', measure=measure, lower=rng.randrange(5), upper=rng.randrange(8))
                if measure == 'class': r['class'] = f'c{rng.randrange(c)}'
                reqs.append(r)
            p = fixture(counts, k, reqs)
            expected = oracle(p)
            result = solve(p, max_nodes=100000)
            self.assertEqual(result['status'], 'FEASIBLE' if expected is not None else 'INFEASIBLE', case)
            if expected is not None:
                self.assertTrue(check_assignment(p, result['assignment'])['valid'])
            for budget in (0, 1, 3):
                limited = solve(p, max_nodes=budget)
                self.assertLessEqual(limited['nodes'], budget)
                self.assertIn(limited['status'], (result['status'], 'UNKNOWN'))

    def test_forged_problem_cannot_bypass_validation(self):
        from dataclasses import replace
        from foldcontract.solver import solve
        from foldcontract.model import ValidationError
        p = fixture([[1], [2]])
        for bad in (replace(p, digest='0' * 64), replace(p, counts=((-1,), (2,)))):
            with self.assertRaises(ValidationError):
                solve(bad)
