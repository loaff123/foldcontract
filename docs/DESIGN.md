# FoldContract v0 implementation contract

Status: implemented alpha contract following a throwaway feasibility study. The resource details in [RESOURCES.md](RESOURCES.md) supersede the original proposal: the deadline is parent-observed, file I/O/publication are excluded, and aggregate parent/process-tree memory is unsupported.

## Purpose and boundaries

Construct a group-disjoint dataset partition satisfying explicitly requested class coverage and size bounds, or distinguish completed infeasibility from an unfinished search. This is a useful research/developer preprocessing tool. It never fits a model, selects splits using downstream scores, relabels classes, breaks groups, or relaxes constraints automatically. It does not certify statistical validity or discover missing grouping relationships.

This is a constructive constraint utility, not a generic approximate stratifier or a new exact optimization invention.

## Frozen v0 contract

Input: a bounded JSON document rejecting duplicate object keys and nonfinite numbers with named groups, named classes, a group-by-class nonnegative integer count matrix, 2–8 named partitions, and explicitly named lower/upper requirements on each partition's class counts, record totals, or group counts. K-fold is K jointly assigned partitions; its preset explicitly adds a named minimum of one group per test partition and records that requirement. The adapter returns each partition as test and its complement as train only when all required partitions are nonempty. Direct partition planning may permit empty partitions only when its explicit requirements do. A direct sklearn adapter for a supplied core result rejects an empty train or test partition; it never silently adds requirements. The stronger K-fold preset is an explicitly selected and documented input-construction step. At least2 partitions are required. Every listed group must contain at least1 record; every listed class must occur. IDs are unique nonempty strings with bounded UTF-8 length and compared exactly, without normalization. Booleans/floats are rejected as counts. Bounds outside available totals remain well-formed but can be infeasible. An empty bounds set is legal and imposes no hidden balance objective. Repeated named requirements on the same partition/measure are a conjunction (effective lower=max lower bounds, upper=min upper bounds), with original names preserved for core reduction. Duplicate requirement IDs are rejected.

Initial supported profile:1–128 groups,32 classes,8 partitions,1024 named requirements, count totals<=1,000,000,000, input<=4MiB and output<=8MiB. IDs are at most128UTF-8 bytes, JSON nesting at most6, and numeric tokens at most10decimal digits before integer parsing. These are admission limits, not completion guarantees. The pre-probe protocol discussed an intended64-group profile;128 is a post-probe admission choice supported only by tested40/80/160-group fixtures, with known UNKNOWN results at80. It is not a completion-reliability claim.

Default limits are100,000 search nodes, a10-second parent-observed operation deadline, and512MiB Linux worker address-space. Operation timing covers worker startup, parsing, normalization, solving, worker serialization, and observed parent decode/verification/serialization checks. Bounded synchronous parent steps are not preempted; input file reading, interpreter bootstrap memory, aggregate parent/process-tree memory and external output publication have explicit unsupported/excluded scopes. This is not a hard end-to-end real-time or aggregate-memory guarantee. The parent independently rechecks any returned witness before accepting FEASIBLE. Known node exhaustion, observed timeout and reported allocation exhaustion return UNKNOWN with machine-readable reasons. An unexplained OS kill returns ERROR; it is not guessed to be memory exhaustion. Unexpected engine crashes, malformed worker output or protocol errors return a distinct ERROR state and must not be counted as successful honest resource handling. Invalid input is a separate validation error. The actual resource enforcement backend/unsupported flags are exposed and verified per platform; platform absence is never silently reported as enforced. No URLs, callbacks, pickle, dynamic imports or payload execution.

Output statuses:
- FEASIBLE: exactly one partition for every group; recomputed exact counts meet every requirement; independently verifiable witness
- INFEASIBLE: all assignments eliminated by sound exact search; may include a bounded conflicting-requirements subset
- UNKNOWN: timeout, node/memory limit, interruption or unsupported verification state; it does not mean infeasible

An existing assignment can be checked without solving. The public check command verifies FEASIBLE witnesses only; an INFEASIBLE/UNKNOWN report cannot be validated merely from a digest or asserted status. Re-establishing infeasibility requires a separate solve with the declared original constraints. Check results name the violated bounds and exact observed counts. Provenance records the problem digest and engine/profile version, not original row contents.

## Algorithm

Each group has a bitmask domain of partitions. Every named bound is a nonnegative-integer linear sum for one partition. Propagation computes forced and possible totals, detects contradictions, excludes assignments exceeding an upper bound, and forces assignments necessary to reach a lower bound. Search branches on the smallest remaining domain and a deterministic rarity heuristic. Arithmetic affecting feasibility is integer-only. No heuristic is allowed to turn search exhaustion into infeasibility. A dead-state cache is optional and limited to4096 entries plus a16MiB conservative accounting budget; stop adding entries when either cap is reached. These internal quotas supplement the enforced worker memory limit. Unbounded memoization is not permitted.

The prototype uses floating-point heuristic ordering only; production should replace it with an exact deterministic ranking or explicitly pin ordering semantics. The prototype is not reused as production code without test-first reimplementation.

Balance is not an optimization claim. Users request size bands explicitly. Optional supplied candidate assignments are accepted only after full independent checking; this can admit a useful heuristic result without trusting its internals. No extra optimizer in v0.

## Explanations

For an infeasible problem, deletion-based reduction attempts removing named requirements while keeping group indivisibility as fixed semantics. Retain a requirement whenever the reduced problem is feasible or UNKNOWN; remove it only after completed infeasibility. A core is called deletion-minimal only if every single retained requirement's removal has a independently checked feasible witness. Otherwise label it sufficient, minimality unproven. It is never claimed minimum-cardinality. The original full problem remains unchanged.

The default explanation budget is separate and bounded. Infeasibility and explanation completeness are separate fields. A cooperative timeout during reduction cannot invalidate the completed original infeasibility result, but cannot justify a stronger minimality claim. The supervised parent preserves a completed INFEASIBLE report once it receives and binds the complete report to the original problem digest. If the watchdog kills the worker before receiving a complete report, the parent returns UNKNOWN, because it did not observe any original result. Explanation reports list relevant groups and class totals so users can decide how to change their request. It never recommends altering study design automatically.

## Separation and independent checks

Modules: strict model/admission; normalization and named constraints; integer solver; standalone assignment checker; core reducer; worker/resource boundary; CLI; optional sklearn adapter. The assignment checker must not call the solver or trust output totals. It independently groups input counts by returned partition. Solver completion tests use independent Cartesian enumeration and separately modeled MILP/HiGHS in development only.

v0 input is aggregated class counts. A small explicit y/groups adapter may aggregate ordinary single-label rows but does not inspect features; row-level metadata or arbitrary dataset readers are out of scope. Multilabel and weighted records are deferred to avoid ambiguity about record totals.

## Error and reproducibility behavior

Output is deterministic for fixed normalized input, node budget and engine version when no walltime limit is reached. A walltime-bound outcome may vary by machine; never claim universal reproducibility for timeout status. Canonical problem IDs/partition names determine tie order. The report clearly distinguishes source-row order from group ordering.

For output checking, reject missing/extra/duplicate group assignments, unknown partition names or digest mismatch. Only FEASIBLE witnesses are independently checkable without solving. Stored INFEASIBLE/UNKNOWN statuses remain unverified assertions unless replay establishes a result; replay UNKNOWN neither confirms nor refutes an earlier status. A digest binds identity and detects accidental change, but is not authentication. An infeasibility result is a completed solver result, not a portable formal proof; replay is recomputation and must say so. No cryptographic proof claim.

## Verification approach

1. Spec review and model/checker. Freeze schema, named-bound semantics, limits and examples. Test malformed data, booleans, duplicate IDs, overflow bounds and assignment tampering. All examples use generic non-sensitive data.
2. Test-first solver implementation. Start with enumerated small problems, odd-cycle contradiction, asymmetric partitions, capacities, zero bounds and budgets. Exhaustively cross-check a separate oracle; tests must exercise each pruning rule and UNKNOWN propagation.
3. Explanations. Add deletion reduction and independent witnesses for each kept-bound removal. Include one nontrivial coverage conflict and one coverage/size conflict. Never label a timeout core minimal.
4. Integration/resource reliability. CLI validate/solve/check/explain, optional sklearn adapter, worker watchdog, package build/install and deterministic node-budget reruns. Verify no network or payload execution. Add stress cases that honestly return UNKNOWN.
5. Independent review and release gate. Compare the frozen research cohort without dropping failures; preserve source versions, runtimes and all raw results. Disclose MILP performance advantages. Independent source/package review, build checks and clean installation are separate from public CI. Only completed, observed checks may be reported as passed.

Target usable deliverable: pip-installable dependency-free core, optional sklearn adapter, short examples with real failure semantics, source-backed prior-art comparison, and independently reproducible experiments. Platform support, resource enforcement and public CI are claimed only when independently observed.
