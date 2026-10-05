# Resource and publication contract

## Supported supervised backend

The supervised API (`run_bytes`, `run_check_bytes`) and CLI currently support
Linux with Python's `resource.RLIMIT_AS` and `/proc/self/statm`. Linux was tested
in the recorded environment below. macOS and Windows were not tested and are
rejected with `ERROR / UNSUPPORTED_MEMORY_BACKEND`; no equivalent hard-limit
support is claimed. Failure to install the Linux limit is an `ERROR`, not a
successful run with a silently missing limit.

The default memory request is **512 MiB of worker virtual address space**.
`RLIMIT_AS` limits the worker's mappings and allocations after installation. It
is not an RSS target. The worker measures its bootstrap address-space size before
installation. A requested limit no larger than that already-observed baseline
returns `UNKNOWN / MEMORY_LIMIT`; the report does not claim the limit was
installed or that existing memory was reduced. Allocation failure under the
installed limit also returns `UNKNOWN / MEMORY_LIMIT`.

The following memory scopes are explicitly **not capped**:

- The calling Python process and its existing objects, imports and input bytes
- The worker interpreter and small stdlib bootstrap before limit installation
- An aggregate total across the caller, worker and any other processes

The parent may therefore already use more than 512 MiB, or caller plus worker
may exceed 512 MiB, without contradicting the implemented worker-only cap. This
release does not offer a whole-tree or parent-memory enforcement mode. A user
requiring that scope needs an independently configured OS/container limit; this
API does not install or verify one. Metadata contains the requested byte limit,
actual worker acknowledgement, and unsupported scopes. If a worker is killed
before acknowledgement arrives, `enforced: false` means enforcement was not
confirmed to the supervisor; it must not be read as proof that no cap was active.

The fixed worker runs `python -I -S` with a package-owned entry point. It never
loads a payload-selected module, URL, callable, callback or pickle, and never
intentionally starts descendants. A new process group permits watchdog cleanup
of the worker and descendants still in that group. Arbitrary escaped descendant
trees are unsupported; this is not a sandbox for untrusted executable code.
Normal child completion is reaped. Timeout cleanup is tested independently with
a test-owned worker that starts a sleeping descendant.

## Admission and allocation bounds

| Quantity | Bound |
| --- | ---: |
| Problem bytes | 4 MiB |
| Report/output bytes | 8 MiB |
| Captured worker stderr | 64 KiB |
| Groups / classes / partitions / requirements | 128 / 32 / 8 / 1,024 |
| Identifier UTF-8 bytes | 128 |
| Total input records | 1,000,000,000 |
| Problem JSON nesting / integer-token digits | 6 / 10 |
| Worker protocol and checked-report nesting | 16 |
| Protocol integer digits / numeric token characters | 20 / 64 |
| Search nodes, default / maximum | 100,000 / 10,000,000 |
| Explanation removal checks, default / maximum | 1,024 / 1,024 |

Pipe output is incrementally drained with bounded buffers; `communicate()` is
not used to accumulate unbounded output. JSON nesting and numeric token limits
are checked lexically before decoding. Duplicate keys and nonfinite values are
rejected. The parent still allocates decoded objects and independently
normalized input within byte/count admission limits. Those allocations are
bounded by admission, **not by an enforced parent-memory quota**. Input creation
by an API caller is outside the operation's allocation accounting.

`run_check_bytes` admits a separate problem of up to 4 MiB and report of up to
8 MiB. The parent combines them into a bounded, length-framed 12 MiB maximum
stdin stream for the same fixed worker. Check does not run the solver. CLI file
reads stop after their cap plus one byte.

## Wall time and completed results

The default timeout is ten seconds; accepted values are finite numbers from
zero through 86,400 seconds. The parent starts a monotonic deadline at supervised
API entry. It watches worker startup, parsing, normalization, solve/explain/check,
worker serialization, protocol decoding, parent normalization and verification,
and a parent serialization-size check.

This is an **observed deadline, not an absolute real-time completion guarantee**:

- Synchronous bounded parent parsing, checking and serialization are observed
  before/after stages, not forcibly preempted inside Python
- OS scheduling, subprocess creation and cleanup may overrun the requested time
- The input bytes already exist when the API starts; CLI file reads are outside
  its deadline and can be slow or block on unusual filesystem objects
- CLI/output-file publication, caller serialization, and stdout backpressure are
  outside that deadline; there is no absolute deadline across publication

A deadline reached without an accepted completed result yields `UNKNOWN /
TIMEOUT` (a cooperative core result may use lowercase `timeout`). A complete
`INFEASIBLE` report whose schema, engine version, exhausted-search reason and
original problem digest have already been verified remains `INFEASIBLE` if later
parent work observes the deadline was exceeded. The report records
`deadline_exceeded` and `completed_infeasibility_preserved`. Completed minimality
facts are retained; incomplete reduction stays explicitly incomplete. An abrupt
kill before such a report is received cannot preserve an unseen conclusion and
returns `UNKNOWN`. An INFEASIBLE report is a trusted completed solver result,
not a portable formal proof or an independently established parent result.

A `FEASIBLE` report is accepted only after the parent independently normalizes
the original input and recomputes the assignment's constraints. If the observed
deadline expires during that work, the supervised result is `UNKNOWN`, even if
the worker previously found a witness. `VALID` summaries are checked against
parent-normalized input. Explanation structure, retained requirement IDs, check counts, witness assignment
shapes, minimality-flag consistency, relevant groups and class totals are validated
against the original problem in the parent. Removal witnesses are independently
checked against constraints by the checker inside the worker; this release does
not repeat that full constraint verification in the parent. Metadata exposes this distinction.

Unexpected process exits, OS kills with no known cause, malformed protocol,
invalid returned witnesses, and unexpected engine exceptions are `ERROR`.
An unexplained `SIGKILL` is never guessed to be memory exhaustion. Known node,
wall or allocation exhaustion is `UNKNOWN`. Neither means infeasible.

The low-level `solver.solve` and `explain.explain` interfaces are separately
available, including on other Python platforms, but only implement node budgets
and optional cooperative deadlines. They install no process, hard-memory limit
or watchdog. Cross-platform execution of that core is not claimed as tested by
this Linux resource calibration.

## Output-file commit

`--output` publishes into the target directory with a unique temporary file,
flush/fsync, then atomic hard-link creation of the final name. It does not use an
`exists()` check followed by replacement. The temporary file is removed afterward.

An existing file, hard-link alias of an input, directory, symlink, dangling
symlink, or competing output creation wins: the new publication fails with exit
5 and the existing object is preserved. Input files are never overwritten.
Filesystems without the required hard-link semantics fail closed; there is no
unsafe fallback to replacement. Once the final link exists, a later cleanup
error can report failure even though publication happened; inspect the target
before retrying. This provides atomic no-clobber visibility, not a guarantee of
directory-entry durability across power failure. Final-name publication is not
covered by an absolute wall deadline.

## Local measurements and regression evidence

`tools/calibrate_resources.py` reproduces `evidence/runtime-calibration.json`.
The recorded run used Python 3.12.14 on Linux x86_64. A 529,223-byte valid fixture
simultaneously exercised 128 groups, 32 classes, 8 partitions, 1,024 named
requirements, 128-byte identifiers and exactly one billion records.

| Case | Observed outcome | Seconds | Sampled worker virtual / resident peak |
| --- | --- | ---: | ---: |
| Maximum admitted fixture, validate | VALID | 0.598 | 40.5 / 14.6 MiB |
| Same fixture, one search node | UNKNOWN, node_limit | 0.110 | 41.1 / 15.6 MiB |
| Exactly 4 MiB of malformed JSON | VALIDATION_ERROR | 0.478 | 45.2 / 19.6 MiB |

Every run acknowledged the 512 MiB worker limit; all workers were reaped and
`/proc` entries were absent after return. The parent operation-only Python
allocation peaks were approximately 2.93 MiB, 84 KiB and 82 KiB respectively.
These exclude existing caller state and input construction. Parent cumulative
RSS and full runtime metadata are recorded in the JSON. No measured quantity is
presented as an enforced caller or aggregate quota.

Worker peaks were sampled every two milliseconds and can miss short-lived
peaks. Timing includes measurement overhead and is a local observation, not a
completion guarantee. A separate regression test installs a real 48 MiB worker
address-space limit and attempts a 64 MiB allocation: the allocation fails and
the worker reports acknowledged `UNKNOWN / MEMORY_LIMIT`. A one-MiB request also
tests the explicit bootstrap-baseline rejection. Tests cover crash distinction,
malformed/oversized output, stderr bounds, parent verification timeout, output
races, symlinks and process-group cleanup. Historical RED/GREEN evidence is under
`evidence/`; `python -m unittest discover -s tests -v` is the source test command.
