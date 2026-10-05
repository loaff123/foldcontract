"""Exact, bounded finite-domain search over nonnegative integer measures.

This module has cooperative deadlines only. Use runtime.run_bytes for a
subprocess watchdog. Exhausted search is a result, not a portable proof.
"""
from __future__ import annotations

import math
import time

from .checker import check_assignment
from .model import Problem, ValidationError, validated_problem

ENGINE_VERSION = '0.1.0a1'
CACHE_ENTRIES = 4096
CACHE_BYTES = 16 * 1024 * 1024
MAX_NODES = 10_000_000


def _validate_budget(max_nodes: int, deadline: float | None) -> None:
    if type(max_nodes) is not int or not 0 <= max_nodes <= MAX_NODES:
        raise ValidationError('invalid_budget', f'max_nodes must be an integer from 0 to {MAX_NODES}')
    if deadline is not None:
        if type(deadline) not in (int, float) or abs(deadline) > 1e100 or not math.isfinite(deadline):
            raise ValidationError('invalid_deadline', 'deadline must be a finite monotonic time in the supported range or None')


def _constraints(problem: Problem):
    """Merge repeated bounds by conjunction, never by last-write-wins."""
    partition_index = {name: i for i, name in enumerate(problem.partitions)}
    class_index = {name: i for i, name in enumerate(problem.classes)}
    vectors = {}
    bounds = {}
    for req in problem.requirements:
        measure = (req.measure, req.class_name)
        if measure not in vectors:
            vectors[measure] = tuple(
                row[class_index[req.class_name]] if req.measure == 'class'
                else sum(row) if req.measure == 'records' else 1
                for row in problem.counts
            )
        key = (partition_index[req.partition], measure)
        old = bounds.get(key)
        bounds[key] = (max(old[0], req.lower), min(old[1], req.upper)) if old else (req.lower, req.upper)
    result = []
    for (partition, measure), (lower, upper) in sorted(bounds.items(), key=lambda x: (x[0][0], x[0][1][0], x[0][1][1] or '')):
        weights = vectors[measure]
        result.append((1 << partition, lower, upper, tuple((i, w) for i, w in enumerate(weights) if w)))
    return result


def solve(problem: Problem, *, max_nodes: int = 100000, deadline: float | None = None) -> dict:
    """Return FEASIBLE, completed INFEASIBLE, or resource-limited UNKNOWN.

    Each entered search state consumes one node, before propagation. No node
    budget is consumed by input normalization. Integer decisions and canonical
    IDs make node-limited runs deterministic; wall-limited runs can vary.
    Unexpected exceptions deliberately propagate to the supervised boundary.
    """
    _validate_budget(max_nodes, deadline)
    problem = validated_problem(problem)
    constraints = _constraints(problem)
    n = len(problem.groups)
    k = len(problem.partitions)
    support = [sum(row[c] > 0 for row in problem.counts) for c in range(len(problem.classes))]
    rank = [(min(support[c] for c, value in enumerate(row) if value), -sum(row), i) for i, row in enumerate(problem.counts)]
    nodes = 0
    dead = set()
    cache_bytes = 0
    stop_reason = None

    def stop():
        nonlocal stop_reason
        if deadline is not None and time.monotonic() >= deadline:
            stop_reason = 'timeout'
            return True
        return False

    def cache(state):
        nonlocal cache_bytes
        # Conservative per-entry accounting includes tuple, referenced integer
        # objects, set table/load overhead, and an extra fixed safety allowance.
        charge = 256 + 48 * len(state)
        if len(dead) < CACHE_ENTRIES and cache_bytes + charge <= CACHE_BYTES:
            dead.add(state)
            cache_bytes += charge

    def propagate(domains):
        changed = True
        while changed:
            if stop():
                return None
            changed = False
            for bit, lower, upper, items in constraints:
                forced = sum(weight for i, weight in items if domains[i] == bit)
                possible = sum(weight for i, weight in items if domains[i] & bit)
                if forced > upper or possible < lower:
                    return False
                for i, weight in items:
                    domain = domains[i]
                    if domain == bit or not domain & bit:
                        continue
                    if forced + weight > upper:
                        domain &= ~bit
                        if not domain:
                            return False
                        domains[i] = domain
                        changed = True
                    elif possible - weight < lower:
                        domains[i] = bit
                        changed = True
        return True

    def search(domains):
        nonlocal nodes, stop_reason
        if stop():
            return None
        if nodes >= max_nodes:
            stop_reason = 'node_limit'
            return None
        nodes += 1
        initial = tuple(domains)
        if initial in dead:
            return False
        consistent = propagate(domains)
        if consistent is None:
            return None
        if not consistent:
            cache(initial)
            return False
        unassigned = [i for i, mask in enumerate(domains) if mask & (mask - 1)]
        if not unassigned:
            return tuple(mask.bit_length() - 1 for mask in domains)
        chosen = min(unassigned, key=lambda i: (domains[i].bit_count(), rank[i]))
        options = []
        for partition in range(k):
            bit = 1 << partition
            if not domains[chosen] & bit:
                continue
            gain = 0
            for constraint_bit, lower, _upper, items in constraints:
                if constraint_bit == bit:
                    forced = sum(w for i, w in items if domains[i] == bit)
                    weight = next((w for i, w in items if i == chosen), 0)
                    gain += min(weight, max(0, lower - forced))
            assigned = sum(mask == bit for mask in domains)
            options.append((-gain, assigned, partition))
        for _, _, partition in sorted(options):
            branch = domains.copy()
            branch[chosen] = 1 << partition
            found = search(branch)
            if found is not False:
                return found
        cache(initial)
        return False

    found = search([(1 << k) - 1] * n)
    common = dict(problem_digest=problem.digest, nodes=nodes, engine_version=ENGINE_VERSION)
    if found is None:
        return dict(common, status='UNKNOWN', reason=stop_reason)
    if found is False:
        return dict(common, status='INFEASIBLE', reason='exhausted_search')
    assignment = [dict(group=group, partition=problem.partitions[partition]) for group, partition in zip(problem.groups, found)]
    checked = check_assignment(problem, assignment)
    if not checked['valid']:
        raise RuntimeError('engine produced an invalid assignment')
    if stop():
        return dict(common, status='UNKNOWN', reason=stop_reason)
    return dict(common, status='FEASIBLE', reason='checked_witness', assignment=assignment)
