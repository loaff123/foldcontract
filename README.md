# FoldContract

Build a group-disjoint dataset partition that satisfies named integer requirements, or distinguish an exhausted infeasible search from a budget-limited unknown.

FoldContract is a small, dependency-free contract and explanation utility. It does not optimize balance, fit models, choose seeds using model scores, or certify statistical validity. Every input group stays intact. Requested requirements are never silently relaxed.

**Alpha:** the production validation evidence and tested platform scope are in [Evaluation](docs/EVALUATION.md). The original research found compiled SciPy/HiGHS MILP substantially faster and able to decide cases this Python engine could not. Prefer a mature solver when throughput or scale matters.

## Install

The supervised CLI currently supports Linux only. The in-process core uses Python 3.10 or later syntax; only versions explicitly recorded in the evaluation have been tested.

```sh
python -m pip install .
foldcontract --help
```

The core has no runtime dependencies. NumPy, SciPy and scikit-learn are not imported by the package. Development comparisons can use separately installed SciPy/scikit-learn.

## A contract

Save this as `problem.json`:

```json
{
  "version": 1,
  "groups": ["batch-a", "batch-b", "batch-c", "batch-d"],
  "classes": ["red", "blue"],
  "partitions": ["train", "test"],
  "counts": [[2, 0], [0, 2], [1, 0], [0, 1]],
  "requirements": [
    {"id": "test-red", "partition": "test", "measure": "class", "class": "red", "lower": 1},
    {"id": "test-blue", "partition": "test", "measure": "class", "class": "blue", "lower": 1},
    {"id": "test-size", "partition": "test", "measure": "records", "lower": 2, "upper": 3},
    {"id": "train-groups", "partition": "train", "measure": "groups", "lower": 1}
  ]
}
```

```sh
foldcontract validate problem.json
foldcontract solve problem.json --output split.json
foldcontract check problem.json split.json
foldcontract solve problem.json --max-nodes 0  # deliberately UNKNOWN
foldcontract explain examples/infeasible-coverage.json
```

Output files are created atomically without overwriting an existing file, directory or symlink. Use a new filename for every result. The input is never modified. Shell redirection is outside this protection: do not redirect output onto an input or existing file.

## Read results carefully

- `FEASIBLE`: every group has exactly one partition and an independent checker has recounted every requirement
- `INFEASIBLE`: this engine completed exact elimination of all assignments. This is a solver result, not a portable formal proof or cryptographic certificate
- `UNKNOWN`: a declared search/resource budget prevented an answer. It never means infeasible
- `VALIDATION_ERROR`: the input or requested options violate the supported contract
- `ERROR`: unexpected engine/protocol failures or unsupported requested enforcement. A crash is not relabeled a successful resource-limited result

Exit codes are 0 for feasible/valid, 2 for infeasible, 3 for unknown, 4 for invalid input/witness, and 5 for engine/I/O errors. `check` accepts only FEASIBLE witnesses. It cannot certify a saved INFEASIBLE or UNKNOWN report; rerunning the solver is recomputation. An inconclusive replay neither confirms nor refutes an earlier claim. SHA-256 problem digests detect identity mismatch, not impersonation or malicious tampering.

## Python core

```python
from foldcontract.model import parse_problem
from foldcontract.solver import solve
from foldcontract.checker import check_report

problem = parse_problem(open("problem.json", "rb").read())
report = solve(problem, max_nodes=100_000)
if report["status"] == "FEASIBLE":
    assert check_report(problem, report)["valid"]
```

The low-level core is in-process, with a node budget and optional cooperative monotonic deadline. It does not enforce memory or kill a blocked caller. For a supervised child use `foldcontract.runtime.run_bytes`, or the CLI. See [resource semantics](docs/RESOURCES.md) before using it on untrusted inputs or depending on a deadline.

## Named conflict explanations

`explain` first establishes the original result, then tries deleting requirements using a shared node budget and a bounded number of removal checks. The core reducer preserves an already completed INFEASIBLE result even if reduction is incomplete. The supervised API preserves that result once a complete, digest-bound report reaches the parent; a watchdog kill before any complete report arrives returns UNKNOWN because no original result was received. The original problem and names stay unchanged.

`explanation.requirement_ids` identifies a sufficient conflicting subset. `minimal: true` is emitted only when every retained requirement's removal has an independently checked feasible assignment in `removal_witnesses`. This means deletion-minimal, never minimum-cardinality or unique. `minimal: false` means minimality remains unproven. The group-indivisibility semantics remain fixed during every trial.

The odd-cycle example has three groups containing pairs of three classes and two partitions requiring each class. Each class appears in two groups, but satisfying the three pairwise separation requests would require a two-coloring of a triangle. Marginal rarity checks alone miss the conflict.

## Exact input semantics and bounds

All fields are explicit. Unknown fields, duplicate object keys or IDs, booleans/floats as counts, NaN/Infinity, empty groups/classes and invalid UTF-8 are rejected. IDs compare exactly without Unicode normalization, so visually similar IDs can be distinct. Inputs and reports are ordinary JSON, never code, pickle, URLs or callbacks.

The single-label count matrix has one nonnegative integer count per group/class. Group record totals are row sums. Every listed group must have at least one record and every class must occur. Requirement measures are `class`, `records`, and `groups`; omitted lower/upper endpoints default to zero/the available measure total. At least one endpoint must be supplied. Bounds outside available totals and inverted bounds are well-formed but may be infeasible.

Repeated requirements for a partition/measure are a conjunction, with all names retained for explanation. An empty requirements list adds no balance or nonempty-partition rule. The explicitly selected K-fold row preset adds named group minima; a supplied empty test/train fold is rejected by the adapter rather than repaired. See [row adapters](docs/ADAPTER.md).

Admission limits: 1–128 groups, 1–32 classes, 2–8 partitions, 1,024 requirements, 1,000,000,000 total records, 128 UTF-8 bytes per ID, 4 MiB JSON input, nesting depth 6, and 10 decimal digits per integer token before integer decoding. Supervised outputs are capped at 8 MiB; explanation removal-witness payloads have a separate 4 MiB quota. Search defaults to 100,000 nodes; maximum selectable node budget is 10,000,000. The bounded dead-state cache admits at most 4,096 states and uses a conservative 16 MiB accounting quota. These are admission/allocation limits, not completion guarantees. Some much smaller instances can be harder than larger ones.

Normalized group, class, partition and requirement IDs are sorted lexicographically; returned assignments use that group order. Source row order is separately preserved by adapters. With the same normalized input, engine and node budget, no wall deadline, solver decisions are deterministic and use only integers. Wall-limited outcomes depend on the machine and scheduling.

## Reproduce and compare

```sh
python -m unittest discover -s tests -v
python tools/reproduce_cohort.py --help
```

See [Evaluation](docs/EVALUATION.md) for frozen synthetic fixtures, independent enumeration, known UNKNOWN results, platform/resource qualifications and unfavorable baseline comparisons. The reported seven medium hard-coverage wins are synthetic cases against 17 SGKF attempts, not seven production incidents. Historical scikit-learn #28218 passes default SGKF and 10/17 attempts in the research setup.

Alternatives include [MSTTS](https://github.com/dsl-unibe-ch/mstts) for CP-SAT grouped holdout balancing after aggregation, [SplitProof](https://github.com/appleweiping/splitproof) for deterministic grouped splitting/manifests, [splitutils](https://github.com/reichelu/splitutils) for checked candidate sampling, and broader [DataSAIL](https://datasail.readthedocs.io/) similarity-aware splitting. Exact constraint solving and split checking are established techniques. FoldContract's value is the small explicit K-way contract, checked witness, distinct outcomes and named explanations together. No novelty, superior speed, adoption or statistical-performance claim is made.

MIT licensed. No telemetry or runtime network access is implemented.
