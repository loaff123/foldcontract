# Evidence index

Final factual records:

- `oracle-production-summary.json` and `oracle-production-results.jsonl`: unchanged 752-row cohort replay, including UNKNOWN and UNSUPPORTED outcomes
- `oracle-adversarial-summary.json` and `oracle-adversarial-results.jsonl`: independent raw-schema Cartesian comparisons, low-budget calls and core checks
- `oracle-artifact-manifest.json`: frozen experiment artifact hashes
- `review-corpus-summary.json`: separately written release-review oracle outcomes
- `runtime-calibration.json`: maximum admission-profile and parent/worker resource observations
- `adapter-row-cap.json`: ordinary million-row adapter observation
- `installed-wheel-tests.log`: final maintainer full suite against installed code, with no source package in the test directory
- Package audit and final reviewed-source hashes are recorded by the independent release reviewer

Files containing `research` retain the original throwaway study. The protocol
title was normalized for publication by removing an internal ordinal; the
methodology, numerical fixtures, data, outcomes and runtime code are unchanged.
The public research and artifact manifests hash this presentation-only derivative.
`prehardening`, `initial`, and `pre-final` identify earlier snapshots and are not
substituted for final source-matched evidence. RED logs intentionally record
failures before the associated implementation/fix; GREEN/final logs show the
subsequent checked outcomes. During parallel development, intermediate
integration logs can contain tests whose corresponding feature was still being
implemented. The release review and final installed-package logs determine the
release gate, not an arbitrary historical log.

Published text logs replace build-machine paths with neutral placeholders.
The raw generic fixtures, numeric outcomes and canonical file hashes are not
rewritten by that path normalization. Timing and memory measurements are local
observations, not resource guarantees or evidence of production adoption.
