# Evaluation and reproducibility

FoldContract is a bounded constraint constructor. These experiments test whether
it meets explicitly supplied integer bounds and reports unfinished work honestly.
They do not establish statistical validity, better predictive accuracy, adoption,
solver novelty, best stratification, or general scalability.

## Reproduce from a checkout

The default developer checks use only the Python standard library and the local
package. No prototype module, downloaded code, private path, or network access is
used. From the repository root:

```sh
PYTHONPATH=src python -m unittest discover -s tests -p test_independent_oracle.py -v
PYTHONPATH=src python tools/reproduce_cohort.py
```

The second command runs the supplementary adversarial checks and then every row
of the frozen cohort, writing separate `evidence/oracle-production-*` and
`evidence/oracle-adversarial-*` artifacts. To preserve the checked-in production
run, pass `--output /tmp/foldcontract-replay`. Use `--mode cohort` or
`--mode adversarial` to run one part. `--help` documents the inputs and budgets.
The default cohort budget is 100,000 search nodes and a cooperative five-second
monotonic deadline **per solve**. This script exercises the low-level core, not
the supervised runtime's input/output or operating-system resource isolation.
Its Cartesian oracle has no artificial search budget, but only enumerates small
inputs. Completion and wall-time status can vary across machines.

An additional independent binary MILP model is available if SciPy is already
installed:

```sh
PYTHONPATH=src python tools/reproduce_cohort.py --mode cohort --fresh-milp --output /tmp/foldcontract-milp
```

SciPy is an optional developer oracle, never a runtime dependency. Its model
uses one binary variable per group/partition, exactly-one constraints per group,
and the original additive bounds. Feasible assignments are recounted with exact
Python integers. HiGHS numerical infeasibility status is not an exact portable
proof certificate. The script does not install anything.

## Frozen research cohort and exact translation

All **752** original rows are bundled byte-for-byte as
`evidence/oracle-frozen-cohort.json`. Its SHA-256 is:

```text
766577906c518fe1fdd28d99603dbb5227c4235fcde267f9fc15b58c914fc4cf
```

The original freeze record, protocol, independent oracle output, prototype output,
comparison summary and software-version record remain separately under
`evidence/oracle-research-*`. The research manifest records their byte counts and
hashes. The reproduction script verifies the exact cohort, independent-output
and prototype-output hashes, unique row IDs, and matching 752-row coverage before
comparison. It never replaces old statuses with production statuses.

The research schema names a class count `classes` and sometimes appends a total
record column to its count matrix. Translation:

1. Keep exactly the first `classes` columns as class counts
2. Assert any appended column equals the sum of the original class columns
3. Translate every old partition/dimension lower and upper bound into one named
   class requirement, or a records requirement for the appended total
4. Preserve each bound's exact integer values, without adding group minima,
   balancing objectives, symmetry constraints or other hidden requirements

Tests compare the original matrix interpretation with the translated dictionary
for every complete assignment in a small sample. They also check every row's
shape, requirement counts, and absence of newly introduced group requirements.

The production admission ceiling is 128 groups. All **ten 160-group fixtures**
remain visible in the output as `UNSUPPORTED`, with the actual validation code.
`UNSUPPORTED` is a replay disposition, not a solver status, an infeasibility
claim, or an omitted row. Those fixtures still retain their research and
independent-oracle results. Admission does not guarantee completion: some
80-group cases remain unfinished at the default budget.

## Independent production checks

`tools/reproduce_cohort.py` contains a new unpruned Cartesian oracle written from
original input dictionaries before the author read or imported production
`solver.py` or the throwaway `probe.py`. The authorship snapshot is recorded in
`evidence/oracle-authorship.json`. The code does not call production normalization,
constraint construction, propagation, checker or search helpers. It enumerates
complete assignments and directly adds input class rows for each partition,
then checks every named requirement separately. Repeated bounds retain their
conjunctive meaning. It uses no symmetry reduction or partial-state pruning.

The recorded supplementary run contains:

- **1,212** cases: 1,200 deterministic random cases and 12 focused edge cases
- **590 feasible / 622 infeasible**, all matching complete Cartesian enumeration
- **6,060** calls with node budgets 0, 1, 2, 3 and 7: 1,278 FEASIBLE,
  2,450 INFEASIBLE and 2,332 UNKNOWN, with no wrong decided status or exceeded
  node budget; budget zero always returns UNKNOWN
- **150** infeasible cores checked independently: the retained set is infeasible,
  every single retained-bound removal is independently enumerated as feasible,
  and every returned removal witness is recounted against that reduced raw input
- Asymmetric partitions, repeated named class/records/groups bounds, one-sided
  defaults, inverted and zero bounds, counts summing to one billion, exact
  distinct Unicode names, and empty partitions when no requirement prohibits them

These are post-protocol adversarial tests, not extra primary-cohort rows. Their
seed is 2026100501. The fast unittest suite exercises 362 of these cases, 1,810
low-node-budget calls, 50 cores, tight shared explanation budgets, canonical
reordering, and translation invariance. Full raw supplementary inputs and
results are in `evidence/oracle-adversarial-results.jsonl`.

## Production replay result

The recorded production replay retains all 752 rows:

| Cohort | FEASIBLE | INFEASIBLE | UNKNOWN | UNSUPPORTED |
| --- | ---: | ---: | ---: | ---: |
| 600 small | 217 | 383 | 0 | 0 |
| 120 medium | 94 | 26 | 0 | 0 |
| 30 stress | 17 | 0 | 3 | 10 |
| Historical #28218 | 1 | 0 | 0 | 0 |
| Odd cycle | 0 | 1 | 0 | 0 |
| **All 752** | **329** | **410** | **3** | **10** |

There are **zero disagreements among 739 decided production results**. The 600
small rows plus the odd cycle were freshly checked by Cartesian enumeration;
medium/stress comparisons use the independently modeled research oracle. Every
stored independent feasible witness, every stored prototype feasible witness,
every stored SGKF validity flag, and every fresh production feasible witness was
recounted from the translated original counts. The fresh optional SciPy 1.17.0 MILP run also decided every original row
(342 FEASIBLE / 410 INFEASIBLE), with zero disagreement, including all ten
160-group rows excluded by production admission. It is recorded within the
production replay artifacts. Its total was 3.27 seconds across all 752 rows;
production spent 19.52 seconds on stress alone and left three admitted cases
UNKNOWN. These observations support no production speed-superiority claim.

The three production UNKNOWN cases in this run are `stress-80-00`,
`stress-80-04` and `stress-80-09`. They are planted-feasible examples. The exact
reasons and node counts are retained, and UNKNOWN is not counted as correct
infeasibility. Production decides both formerly unfinished medium cases; that
does not erase the research failures. Different search order and different wall
budgets/machines can change which difficult rows finish.

The summary records Python version, budgets, module hashes and script hash.
Earlier production runs are retained under `evidence/oracle-prehardening-*`;
they are not substituted for the final source-matched evidence.
Per-case timings include production input normalization and solve, but exclude
fresh Cartesian enumeration and reference checking. These measurements are
single-run diagnostics, not a controlled performance benchmark. Infeasibility
agreement is implementation evidence, not a formal proof of the entire program.
A stored INFEASIBLE claim cannot be verified merely by checking its digest;
replay must re-establish it. A replay returning UNKNOWN is inconclusive.

## Original research findings, including negative evidence

The original throwaway study used Python 3.12.14, NumPy 2.3.5, SciPy 1.17.0 and
scikit-learn 1.8.0. Its four UNKNOWN outcomes are preserved unchanged:

- `medium-019` and `medium-045`: 100,000-node exhaustion; independent MILP infeasible
- `stress-80-01` and `stress-80-04`: five-second experimental deadline; independent MILP feasible

The original prototype decided 748/752 rows. Compiled MILP decided all of them.
On the medium cohort, original prototype total time was **11.36 seconds**, versus
**0.59 seconds** for MILP; on stress it was **15.11 seconds**, versus **1.70 seconds**.
These are unfavorable, single-run, machine-specific prototype results and remain
part of the evidence. They are not recast as production benchmark results, and
no solver-performance superiority is claimed.

For the 120 synthetic medium cases, the original study compared default SGKF and
seeds 0–15, testing exactly the same hard coverage contract. Default SGKF passed
85 cases; any of 17 attempts passed 87; the prototype found all 94 feasible cases.
The seven additional original frozen **synthetic** cases are `medium-000`,
`medium-012`, `medium-015`, `medium-020`, `medium-059`, `medium-101`, and
`medium-119`. Production also finds all seven. These are not seven external
production incidents, adoption examples, or evidence of better statistical
splits. SGKF pursues approximate distribution balance, a different objective.

The historical #28218 fixture passes default SGKF and 10 of the 17 attempts.
It illustrates seed sensitivity; it is **not** a default/best-of-17 win. The
upstream issue was closed. Coverage-only feasible assignments can be unbalanced;
size bounds must be requested explicitly. There was no runtime comparison with
MSTTS or other exact splitting tools.

The defensible scope is a dependency-free, explicit contract with honest status
handling and checked named conflict explanations. Broader workload usefulness,
statistical benefit, production adoption and unrestricted scaling are unproven.
