# Independent FoldContract source and release review

Date: 2026-10-05 UTC

## Current disposition

**PASS for the reviewed local alpha source and artifacts. No remaining blocking source/package finding was found within the reviewed scope.** No solver-soundness discrepancy was found. Remote CI and any external publication remain separate maintainer-controlled gates; this local review does not assert they occurred. This is an independent implementation review with executable regression evidence, not a formal verification claim, security certification, statistical-validity guarantee, or authorization to publish.

The reviewer did not edit production modules. Implementation owners addressed the findings described below; reviewer-owned tests and evidence were rerun against those changes.

## Independently exercised evidence

`tests/test_release_review.py` contains a separately written raw-schema Cartesian oracle and direct witness recount. It does not use the production checker, constraint normalizer, propagator, or requirement reducer as its oracle.

- 1,024 additional deterministic small problems matched full Cartesian enumeration, including repeated named bounds, asymmetric partition bounds, empty allowed partitions, inverted bounds, mixed class/record/group measures, canonical-order permutations, and selected aggregate counts up to one billion
- 4,096 node-budget-limited runs at budgets 0, 1, 2 and 7 never disagreed with a decided exhaustive result and never exceeded the requested node budget
- 128 additional infeasible problems produced independently enumerated infeasible subsets, with each claimed deletion-minimal removal witness recounted against the raw final subset
- Lexical depth and integer-token failures occurred before the JSON decoder was called; escaped delimiters and long numerals inside valid identifiers remained ordinary strings
- Dataclass-forged digest values were rejected before both solve and explain, including an immediately infeasible problem that would not otherwise reach feasible-witness checking
- Reordered source rows preserved row-index order and group disjointness. A core-legal empty partition was rejected by the adapter rather than silently repaired
- Existing input hard links, parent-directory symlink aliases, and a racing final-output symlink could not be clobbered. Temporary files were cleaned after these normal error paths
- Unexpected supervisor exceptions remained ERROR; malformed/wrong-digest INFEASIBLE protocol responses were rejected; malformed report depth and duplicate keys were rejected
- A real isolated worker ignored hostile current-directory/PYTHONPATH `json.py`, `sitecustomize.py`, and `usercustomize.py`
- Static AST inspection found no network, pickle, dynamic import, or eval/exec path in the runtime package. Subprocess execution is a fixed trusted worker path with Python `-I -S`; JSON cannot choose executable code

The final reviewer suite contains 18 tests, including four malformed-explanation subcases. A fresh final-source integration run passed all 165 tests in 6.35 seconds (`evidence/review-final-source-suite.log`). Earlier RED/GREEN runs remain in `evidence/review-adversarial-initial.log`, `evidence/review-adversarial-green.log`, and `evidence/review-explanation-protocol-red.log`. These are post-protocol review cases, not additions to the frozen primary research cohort.

## Correctness assessment

The nonnegative-integer propagation rules are sound: forced totals lower-bound final values; possible totals upper-bound them. An option is excluded only when assigning it would exceed an upper bound, and forced only when excluding it would make a lower bound unreachable. Within-pass stale totals weaken pruning rather than establish an invalid exclusion. Repeated named bounds are combined by maximum lower/minimum upper, while original requirements remain individually checkable and reducible.

Search visits all remaining alternatives unless a budget ends the attempt. Dead states are recorded only for contradictions or completed unsuccessful branching. UNKNOWN is not cached as infeasibility. Heuristic ordering uses integers, canonical identifiers and deterministic tie breaks. Every FEASIBLE witness is independently recounted.

Deletion reduction removes a requirement only after completed infeasibility. A retained removal witness remains valid when later deletions weaken the subset, and the implementation nevertheless rechecks it against the final subset before claiming deletion-minimality. Minimal means deletion-minimal, not minimum-cardinality. Original infeasibility is retained by the in-process explanation even when reduction is incomplete.

## Findings resolved during review

1. **Late observed explanation status:** the supervisor previously converted a completed original INFEASIBLE report into UNKNOWN if a deadline was observed after explanation work. The supervisor now preserves a complete, validated, original-digest-bound infeasibility result and exposes deadline-overrun/preservation flags. The reviewer regression passes
2. **Malformed decided-result protocol:** missing provenance and a wrong problem digest could previously pass as an INFEASIBLE result. Decided results now require valid node accounting, expected engine/reason, a well-formed digest and parent binding to the admitted original problem. VALID summary fields are also checked against the original problem
3. **Malformed explanation protocol:** missing/wrong-type explanations, unknown requirement IDs and unsupported minimality flags were accepted by the supervisor. A bounded schema guard now checks fields, unique original names, count limits, coherent flags/reasons, complete assignment shapes and original group/class totals. Full removal-bound checking remains in the independent checker inside the worker and is not misrepresented as a repeated parent check
4. **Public option exceptions:** unhashable operation arguments and extremely large timeout integers previously escaped structured option validation. They now return VALIDATION_ERROR. An extremely large core deadline raises ValidationError rather than OverflowError

Dataclass public-boundary hardening was already underway when the review began; the reviewer verified it independently rather than counting it as a new review finding.

## Important scope qualifications

- Hard memory enforcement is Linux worker virtual address space after its Python/stdlib bootstrap. Parent memory, interpreter bootstrap memory and aggregate process-tree memory are explicitly unenforced. A low-level in-process core is available on other platforms; the supervised API fails closed when its backend is unsupported
- Byte/depth/token/shape limits bound admitted structures but are not promises of completion or caller-memory usage
- Deadline enforcement is parent-observed. Parent decode, validation and serialization are bounded synchronous steps rather than preempted real-time operations. File reading, external publication, operating-system scheduling and cleanup may exceed the deadline
- Original INFEASIBLE preservation at the supervised boundary requires a complete report to reach the parent. If the watchdog kills the worker before a complete report arrives, the parent has no checkpoint to preserve and returns UNKNOWN
- Worker-group cleanup covers the fixed worker and cooperative descendants in its process group, not arbitrary escaped descendants. The trusted worker creates no descendants
- Atomic output creation uses a temporary inode and hard-link publication; existing targets win. It does not claim crash-durable directory publication, lock the caller's directory tree against an adversary, or protect user-controlled shell redirection
- Input JSON does not execute code. The ordinary in-process Python API is not an object sandbox for caller-defined objects or a defense against a malicious Python environment
- Parent explanation validation checks bounded schema and original name/count binding. Removal-witness constraints are independently recounted inside the worker, not repeated in the parent; metadata and resource documentation expose this distinction
- A matching digest is identity binding, not authentication. Checking an INFEASIBLE claim requires recomputation; the public checker checks only FEASIBLE witnesses

## Evaluation and documentation audit

Resource and evaluation documentation is present and agrees with the qualified implementation. The original absolute-deadline proposal is explicitly superseded. The reviewer independently matched the production replay's model/checker/solver/explanation hashes to current source and inspected the retained 752-row summary: 329 FEASIBLE, 410 INFEASIBLE, three UNKNOWN and ten admission-unsupported 160-group rows. The reviewer did not claim to rerun that entire cohort as part of this review. The independent production-oracle evaluation's exact small-instance checks and fresh optional numerical MILP run are separately recorded.

The seven medium coverage comparisons are described as synthetic fixtures, the historical SGKF example is correctly qualified, and unfavorable MILP performance comparisons and original UNKNOWN outcomes remain visible. No portable infeasibility-certificate, adoption, statistical-validity or speed-superiority claim is supported or made.

## Final package and offline-install audit

An initial clean virtual environment installed the wheel using `pip install --no-index --no-deps`. The environment contained only pip and FoldContract; no runtime dependency was fetched or installed. Tests were copied into a separate temporary tree containing tests/tools/examples/evidence but no `src` directory, and imports were verified to resolve to the installed `site-packages` package. This independently caught stale artifact/runtime differences before release approval. Initial installed failures remain recorded in `evidence/review-installed-initial-suite.log` and are not hidden.

Archive inspection verified safe relative archive members, no symlink or `.pth` payload, the one intended `foldcontract = foldcontract.cli:main` console entry point, no `Requires-Dist` dependencies, and valid wheel RECORD hashes. All ten production Python source files are byte-identical between the reviewed worktree, wheel and source distribution. The source distribution's included files have no worktree content differences and include the latest reviewer tests. It does not include this separately delivered REVIEW.md or later review logs; those additions do not change production source.

Reviewed artifact SHA-256 values:

- Wheel `foldcontract-0.1.0a1-py3-none-any.whl`: `d24aecafc6e290f846bf9a63e1e932331efb371df2f5c6b9a6aaa36ae18ec193`
- Source distribution `foldcontract-0.1.0a1.tar.gz`: `5d78190a7b785334a313db644fa07028a225b5e2fed31c9622617c0fe9aebaa5`

A second, newly created clean virtual environment installed that exact final wheel offline with `--no-index --no-deps`. All **165 tests passed in 6.19 seconds** from a separate no-source temporary test tree. The interpreter imported only the installed FoldContract package; installed distributions were pip and FoldContract. A separate source-distribution check unpacked the exact audited source archive and built its wheel offline with `--no-index --no-deps --no-build-isolation`, using already available build tools. All ten package sources and METADATA matched the original wheel. That derived wheel was installed in a third clean virtual environment, and all **165 tests passed in 6.10 seconds** from the source distribution's own separate no-source test tree. Build dependencies are not bundled, and this is not a claim that a clean source build needs no separately provided build tools.

Derived-wheel SHA-256: `956263afcf7e2b21005d8aa6a6483d93e3e5d10dc5c7f4f5c7523984da750c2c`. Receipt files are `evidence/review-sdist-offline-build.log`, `evidence/review-sdist-derived-audit.json`, `evidence/review-sdist-derived-install.log`, `evidence/review-sdist-installed-environment.json`, and `evidence/review-sdist-installed-suite.log`. The derived artifact is a test build, not a replacement for either approved distribution above.

A separate real installed-console smoke run exercised help, FEASIBLE publication, witness checking, deliberate UNKNOWN, minimal infeasibility explanation, and invalid CLI arguments with their expected exit codes (`evidence/review-final-console-smoke.json`).

Evidence: `evidence/review-package-final-audit.json`, `evidence/review-final-offline-install.log`, `evidence/review-final-installed-environment.json`, and `evidence/review-final-installed-suite.log`. The audit JSON records all reviewed production hashes and confirms that both final independent-oracle summaries refer to the current model/checker/solver/explanation source.

## Remaining external gate and change control

Do not claim remote CI has passed until a real CI run provides that evidence. A checked-in workflow alone is not a passing run. External repository creation, publication, registry upload, release tagging and distribution are outside this review and were not performed by the reviewer.

Any change to production source or packaged content invalidates the exact artifact hash approval above and requires appropriate retesting/re-audit. A documentation/evidence-only repack can be audited separately; it must not silently inherit a previous artifact hash. Local test success and bounded synthetic evidence do not establish production adoption, broad operational reliability or completion on arbitrary admitted inputs.
