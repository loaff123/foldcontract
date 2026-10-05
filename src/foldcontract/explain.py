"""Bounded named-requirement reduction, never a portable infeasibility proof."""
from __future__ import annotations

import json
import time

from .checker import check_assignment
from .model import Problem, ValidationError, with_requirements, validated_problem
from .solver import solve, _validate_budget

MAX_WITNESS_BYTES = 4 * 1024 * 1024


def explain(problem: Problem, *, max_nodes: int = 100000, max_checks: int = 1024,
            deadline: float | None = None) -> dict:
    """Establish the original result, then attempt a deletion-minimal core.

    Node budget is shared by the original solve and all reduction solves.
    max_checks counts removal trials, excluding the original solve. The
    original INFEASIBLE result survives an incomplete explanation. Minimality
    requires an independently rechecked feasible witness for each final
    single-requirement removal. Minimum cardinality is never claimed.
    """
    _validate_budget(max_nodes, deadline)
    problem = validated_problem(problem)
    if type(max_checks) is not int or not 0 <= max_checks <= 1024:
        raise ValidationError('invalid_budget', 'max_checks must be an integer from 0 to 1024')
    result = solve(problem, max_nodes=max_nodes, deadline=deadline)
    if result['status'] != 'INFEASIBLE':
        return result
    names = [requirement.id for requirement in problem.requirements]
    retained = names.copy()
    witnesses = {}
    witness_bytes = 0
    checks = 0
    total_nodes = result['nodes']
    incomplete_reason = None
    for name in names:
        if deadline is not None and time.monotonic() >= deadline:
            incomplete_reason = 'timeout'
            break
        if checks >= max_checks:
            incomplete_reason = 'check_limit'
            break
        if total_nodes >= max_nodes:
            incomplete_reason = 'node_limit'
            break
        trial_ids = [item for item in retained if item != name]
        trial = with_requirements(problem, trial_ids)
        tested = solve(trial, max_nodes=max_nodes-total_nodes, deadline=deadline)
        checks += 1
        total_nodes += tested['nodes']
        if tested['status'] == 'INFEASIBLE':
            retained = trial_ids
        elif tested['status'] == 'FEASIBLE':
            if not check_assignment(trial, tested['assignment'])['valid']:
                raise RuntimeError('invalid core-removal witness')
            charge = len(json.dumps(tested['assignment'], ensure_ascii=True).encode('utf-8'))
            if witness_bytes + charge > MAX_WITNESS_BYTES:
                incomplete_reason = 'explanation_output_limit'
                break
            witness_bytes += charge
            witnesses[name] = tested['assignment']
        else:
            incomplete_reason = tested['reason']
            # All remaining trials share the same exhausted node/deadline
            # budget, so continuing cannot justify further reduction.
            break
    removal_witnesses = []
    for name in retained:
        if name not in witnesses:
            continue
        if deadline is not None and time.monotonic() >= deadline:
            incomplete_reason = 'timeout'
            break
        relaxed = with_requirements(problem, [item for item in retained if item != name])
        if not check_assignment(relaxed, witnesses[name])['valid']:
            raise RuntimeError('invalid retained-bound removal witness')
        removal_witnesses.append(dict(removed=name, assignment=witnesses[name]))
    minimal = len(removal_witnesses) == len(retained)
    result['nodes'] = total_nodes
    result['explanation'] = dict(
        requirement_ids=retained,
        minimal=minimal,
        complete=minimal,
        checks=checks,
        removal_witnesses=removal_witnesses,
        reason='deletion_minimal_checked' if minimal else incomplete_reason or 'minimality_unproven',
        relevant_groups=list(problem.groups),
        class_totals={name: sum(row[i] for row in problem.counts) for i, name in enumerate(problem.classes)},
    )
    return result
