# Security and scope

FoldContract reads bounded aggregate-count JSON and returns aggregate assignments.
It never executes dataset payloads, imports modules named by input, unpickles
objects, or calls runtime network APIs. The supervised worker starts with
Python's isolated/no-site flags and imports this installed package from its own
known location. This is not a general-purpose sandbox for untrusted Python.

Use the CLI or supervised bytes API for untrusted JSON. Ordinary in-process
Python APIs accept ordinary Python values; custom object methods, already
allocated caller data, caller concurrency and the surrounding interpreter are
outside the input JSON threat model. Do not run a modified installation or an
untrusted Python interpreter and assume witness checks authenticate them.

Strict admission, bounded node/cache/output quotas, Linux worker address-space
enforcement, and observed deadlines limit particular resources. They do not
cap the entire caller process or arbitrary escaped process trees. Read
[resource semantics](docs/RESOURCES.md) for unsupported scopes. A SHA-256 digest
binds a report to canonical counts and requirements, not to row contents or a
trusted author. An INFEASIBLE result is not a portable proof.

The CLI creates a new output inode atomically and refuses existing paths.
This does not protect shell redirection, files changed by other programs, or
an output directory controlled by a hostile process. Keep input/output paths
in directories you control. The package does not offer a hard real-time or
power-failure durability guarantee.

Please report reproducible correctness or security failures without including
private row data, credentials, or other secrets. Generic aggregate fixtures
are preferable. This alpha has no security audit or production-adoption claim.
