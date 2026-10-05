# Explicit row adapters

The solver consumes aggregate integer counts. The optional row adapter turns
ordinary, finite Python sequences of single string labels and string group IDs
into those counts. It never reads features, fits an estimator, uses a score to
choose a split, splits a group, or adds a balancing objective.

## Input contract and limits

`y` and `groups` must be non-text `collections.abc.Sequence` objects with equal,
nonzero lengths. Lists and tuples are supported. Generators, mappings, sets,
strings, bytes, and nested/multilabel rows are rejected. Convert array-like
containers explicitly to lists before calling the adapter; NumPy and pandas
are not dependencies and are not needed. Do not mutate input sequences during
a call. The Python API is for ordinary in-process objects, not an object
sandbox; untrusted JSON belongs at the bounded core/CLI boundary.

Every row has exactly one class label and one group ID. Both must be nonempty
strings of at most 128 UTF-8 bytes. IDs are compared exactly: case, whitespace,
and distinct Unicode encodings remain distinct. There is no integer-to-string
conversion, missing-label imputation, weight handling, or normalization.

The row adapter admits at most **1,000,000 rows**, checking source lengths
before iterating or copying rows. This is deliberately separate from the core
aggregate limit of 1,000,000,000 records. Core limits also apply: 128 groups,
32 classes, 2–8 partitions, and 1,024 named requirements. Excess data is
rejected with `ValidationError`, never truncated.

Aggregation uses O(n + g*c) time and O(g*c) auxiliary storage. Returning all
K train/test pairs uses O(k*n) time and storage. At the row cap this can be
substantial: eight folds contain eight million index-list entries overall.
The row cap is an admission bound, not a memory guarantee. These in-process
functions have no wall-clock watchdog or hard memory enforcement; callers
must budget for their own row data and the returned indices.

## Aggregate without adding requirements

```python
from foldcontract.adapter import aggregate_rows

y = ["blue", "red", "blue", "red"]
groups = ["batch-a", "batch-a", "batch-b", "batch-b"]
problem = aggregate_rows(y, groups, ["test", "train"])
assert problem.requirements == ()
```

An empty requirement list really imposes no coverage, minimum size, or balance
requirement. This direct core problem may legally assign all groups to one
partition. Such a result is valid for direct partition planning but is rejected
when converted to cross-validation indices because it has an empty fold.

Add only the bounds your application actually needs:

```python
problem = aggregate_rows(y, groups, ["test", "train"], requirements=[
    {"id": "test-at-least-two", "partition": "test",
     "measure": "records", "lower": 2},
    {"id": "train-at-least-one-group", "partition": "train",
     "measure": "groups", "lower": 1},
])
```

Requirements are a finite sequence of core-schema dictionaries or
`foldcontract.model.Requirement` objects. Their IDs, bounds, and conjunction
semantics are preserved. Invalid entries are rejected by the core validator.
Partition order, group order, class order, and requirement order are canonical
lexicographic exact-ID order in the returned immutable `Problem`.

## Select the stronger K-fold preset explicitly

```python
from foldcontract.adapter import kfold_problem, split_indices
from foldcontract.solver import solve

y = ["blue", "red", "blue", "red"]
groups = ["batch-a", "batch-a", "batch-b", "batch-b"]
problem = kfold_problem(y, groups, n_splits=2, min_class_count=1)
report = solve(problem, max_nodes=100_000)

if report["status"] == "FEASIBLE":
    splits = split_indices(problem, report, y, groups)
    for partition, (train, test) in zip(problem.partitions, splits):
        print(partition, train, test)
elif report["status"] == "INFEASIBLE":
    print("The explicit requirements cannot all hold with these intact groups")
else:
    print("No conclusion within the search budget:", report["reason"])
```

`kfold_problem` constructs input; it does not run the solver. It creates
`fold_0` through `fold_{n_splits-1}` and explicitly names:

- `fold_i:groups:min`: at least one group in that test partition
- `fold_i:class_j:min`: at least `min_class_count` records of canonical class
  `j` in that test partition

Class ordinal IDs keep generated requirement names within the ID limit even
for maximum-length user labels. Each requirement retains the actual class
name. `min_class_count=0` keeps explicit zero class bounds and the one-group
minimum. Bounds larger than available counts remain well-formed and may make
the problem infeasible. Nothing is automatically relaxed.

The preset does not request equal partition sizes or equal class proportions.
If your design calls for size bands, build the explicit requirements with
`aggregate_rows` instead. Feasibility does not establish statistical validity,
proper study design, or the absence of unknown group relationships.

## Convert an existing checked result to row indices

`split_indices(problem, report, y, groups)` returns a list of
`(train_indices, test_indices)` pairs, in `problem.partitions` order. Every
individual index list is in the supplied source row order. The test set is one
whole named partition and train is exactly its complement.

Before returning any split, the adapter:

1. Validates the supplied `Problem`, including its canonical digest
2. Re-aggregates `y` and `groups`, requiring exact group IDs, class IDs, and
   every group-by-class count to match the problem
3. Independently checks the report's FEASIBLE status, matching problem digest,
   complete one-partition-per-group assignment, and every named bound
4. Rejects any empty test set or complementary train set

Reported totals are not trusted. Missing, extra, duplicate, or unknown group
assignments are rejected. INFEASIBLE and UNKNOWN reports cannot be converted
to splits. Errors are `ValidationError` instances with machine-readable
`.code` and human-readable `.message` fields.

Rows may be reordered between aggregation and conversion. The counts must
remain identical and the returned indices refer to the order supplied to
`split_indices`. The digest binds the canonical aggregate problem; it does
**not** identify original row contents, features, metadata, or row order and
is not authentication. Two row sources with the same group/class counts are
indistinguishable at this boundary.

## Optional scikit-learn protocol

`ReportCrossValidator` implements `split` and `get_n_splits` without importing
scikit-learn or NumPy. It accepts an already solved core problem and report;
it never silently solves a different problem or adds nonempty-fold bounds.

```python
from foldcontract.adapter import ReportCrossValidator

# Only after obtaining a FEASIBLE report as above:
cv = ReportCrossValidator(problem, report)
assert cv.get_n_splits() == len(problem.partitions)
for train, test in cv.split(X=None, y=y, groups=groups):
    pass
```

The constructor validates and snapshots the report assignment. Each `split`
call requires explicit `y` and `groups` again and repeats source and witness
validation. `X` is accepted for protocol compatibility and never inspected;
callers remain responsible for aligning features with the supplied rows.
`get_n_splits(X=None, y=None, groups=None)` returns the number of partitions
without reading any of these optional arguments.

For an installed scikit-learn environment using its default metadata-routing
configuration, pass the same ordinary row lists to the estimator API:

```python
from sklearn.model_selection import cross_validate

result = cross_validate(estimator, X, y, groups=groups, cv=cv)
```

Scikit-learn's metadata-routing configuration can change how `groups` must be
provided to that API. FoldContract does not set or alter that configuration.
The adapter only implements the explicit local `split(X, y, groups)` protocol.

## JSON examples and failure semantics

The examples directory contains generic, non-sensitive core inputs:

- `feasible-coverage.json`: both partitions can cover both classes while each
  holds three to five records
- `infeasible-coverage.json`: three pairwise-overlapping groups form an odd
  cycle; both partitions cannot cover all three classes without breaking a
  group
- `infeasible-coverage-size.json`: the required blue class exists only in a
  four-record group, exceeding the test partition's three-record capacity
- `unconstrained.json`: a valid core problem with no implicit coverage or size
  bounds; a legal result may have an empty partition

```console
foldcontract solve examples/feasible-coverage.json
foldcontract explain examples/infeasible-coverage.json
foldcontract explain examples/infeasible-coverage-size.json
foldcontract solve examples/feasible-coverage.json --max-nodes 0
```

The last command deliberately returns UNKNOWN. Exhausting a budget never
means INFEASIBLE. Conflict explanations preserve the distinction between a
sufficient conflicting set and a verified deletion-minimal set; they do not
claim minimum cardinality or supply a portable formal proof.
