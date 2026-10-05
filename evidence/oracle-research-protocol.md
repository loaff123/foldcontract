# FoldContract feasibility protocol

This is a throwaway feasibility study, not the product or a publication. It uses original generic categorical data, never trains models or executes dataset payloads.

Question: can a bounded dependency-free group assignment constructor enforce user-selected integer per-partition class-count and total-size bounds, distinguish completed infeasibility from budget exhaustion, and provide useful results beyond best-effort stratification?

Scope: indivisible groups with nonnegative integer class counts; 2–8 partitions; explicit lower/upper bounds for each class and total records. The v0 output is one satisfying assignment, completed infeasible, or unknown. No optimal balancing, statistical correctness, fairness, causal validity, detection of undisclosed leakage, or general scalability claim. No automatic relaxation, group breaking, relabeling, or seed selection based on fitted scores.

Predeclared probe:
1. Author a standalone integer finite-domain search with sound lower/upper propagation. Independent validation uses direct Cartesian assignment enumeration; medium problems use existing SciPy MILP/HiGHS with independently constructed binary variables and constraints. No third-party source payload execution.
2. Fixed small cohort: Random(20261005), 600 problems, n=3..9 groups, k=2..3, labels=2..5, values sampled at 60% zero otherwise integers1..4; prevent empty groups by inserting1. Force each class into at least one group if absent. Alternate lower-only coverage and capacity bounds floor(total/k)*[0.65,1.35] with integer rounding; all class lower bounds1, upper total. Preserve every problem/result, including infeasible and trivial cases.
3. Current scikit-learn StratifiedGroupKFold comparison: shuffle=False plus seeds0..15, evaluated by the exact same hard constraints. Use published generic #28218 example separately and 120 problems with n=12..30, k=3..5, labels=3..8, same sparse distribution, with coverage only. Every data case stays; no outcome-based tuning. Multiple tries are a competent baseline, not a statistical-performance recommendation.
4. Capacity/stress: 30 planted feasible instances (40,80,160 groups; k=4;8 labels), assign rows round-robin then set constraints to the planted fold counts +/-max(1,10%); all compared with independent MILP (5 seconds each). Solver per case budget 100000 nodes and 5 seconds, report unknown honestly. Count 160-group cases as outside initial intended64-group public profile if needed.
5. Structural infeasibility: an odd-cycle three-group/three-label two-fold instance has enough supporting groups per label, yet no valid coloring. Independent exhaustive verification; deletion-based label-core extraction may be added only if initial probe passes.

GO gates: zero false feasible/infeasible across all decided independent oracle comparisons; bounded unknown behavior proven; at least one original/historical hard-coverage use case corrected; practical value beyond17 seeded SGKF attempts demonstrated on at least one frozen medium case OR independently explained nontrivial infeasibility that heuristics cannot distinguish. Report any runtime disadvantage to compiled MILP. This is not a speed-competition gate; utility must arise from explicit contracts, reliable statuses, diagnosis, and simple adoption.

Independent reviewer must inspect outcomes before a full product is proposed. Broader user workflow/real production adoption remains unproven; fabricated demand is forbidden.
