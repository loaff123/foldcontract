# Build and verify

Only Linux/Python 3.12.14 was exercised in this local release verification.
The workflow's other Python versions remain configured checks until CI runs;
macOS/Windows supervised resource support is not provided.

## Source checks

```sh
PYTHONPATH=src python -m unittest discover -s tests -v
PYTHONPATH=src python tools/reproduce_cohort.py --output /tmp/foldcontract-replay
PYTHONPATH=src python tools/calibrate_resources.py
```

The exact final source test count and package-audit result are recorded in the
release review and evidence. Historical RED logs show intended failures before
implementation; they are not the final test outcome. Earlier installed-candidate
failures are retained explicitly and were corrected before final artifact audit.
Paths in published logs are normalized to avoid exposing the build environment.

## Build offline with existing tools

The observed build environment had setuptools 84.0.0 and wheel 0.48.0. The source
build requires setuptools >=77 and wheel; these are build-time tools, not runtime
dependencies. Install them from your trusted package source if not already
available. Then:

```sh
PIP_NO_INDEX=1 PIP_NO_CACHE_DIR=1 python -m pip wheel . --no-deps --no-build-isolation --wheel-dir dist
python -c "import setuptools.build_meta; print(setuptools.build_meta.build_sdist('dist'))"
```

A build is not a clean-install test. For the wheel, create an isolated virtual
environment, install only the wheel with `--no-index --no-deps`, then copy
`tests`, `tools`, `examples`, and `evidence` into a separate test directory.
Do not copy `src` there. Run the full unittest suite using that environment's
Python with `PYTHONPATH` unset. Confirm `foldcontract.__file__` points into the
virtual environment's site-packages. The CLI subprocess tests resolve the same
imported installation, including isolated worker invocations.

The source distribution can separately be unpacked and built offline with the
already installed build tools, producing a second wheel for an independent
clean install. Compare every Python package member against the reviewed source
and verify the wheel RECORD hashes and expected console entrypoint.

Public CI and repository publication are separate checks. A workflow file,
local package installation, or this document is not evidence of a remote CI run.
