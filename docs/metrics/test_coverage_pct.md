# Metric `test_coverage_pct` (column BB)

**A STATIC estimate of the test-code share — a test-to-code LOC ratio.  This
is NOT runtime coverage: no tests are executed.**  The value is the percentage
of test-file lines among all code lines of the tracked, non-vendored tree,
capped at 100.

```
test_coverage_pct = min(100, round(100 * test_code_lines / total_code_lines))
                    (0 when total_code_lines == 0)
```

Lines are counted as newline bytes, without language parsing.  Deterministic —
no LLM, no network calls.

## File selection

The input is the FULL tracked-file list of the repository (`git ls-files` /
`hg manifest`; in plain-directory mode a filesystem walk that skips
dot-directories).  The truncated diagnostic `file_tree` (40 paths) is NOT
used.  From that list a file counts toward the denominator only if all of the
following hold:

- **not vendored** — no path segment equals one of: `node_modules`, `vendor`,
  `Pods`, `.venv`, `venv`, `site-packages`, `bower_components`, `DerivedData`,
  `Carthage`, `.dart_tool`, `.gradle`, `__pycache__`, `.tox`, `Godeps`,
  `dist`, `build` (segment match, not substring — `src/vendor_utils.py` is
  kept);
- **a code extension** — `py pyi js jsx mjs cjs ts tsx vue svelte java kt kts
  scala groovy swift m mm h hpp c cc cpp cxx cs go rs rb php dart clj cljs fs
  fsx vb ex exs erl hs lua pl pm r jl sh bash zsh sql html css scss sass less
  feature` (lock files, JSON/YAML fixtures, Markdown, SVG, sourcemaps and
  other text would dilute the denominator — heavily so for frontends);
- **not generated** — the name does not match `*.min.js`, `*.min.css`,
  `*.map`, `*.snap`, `*.lock`, `*.d.ts`, `*_pb2.py`, `*_pb2_grpc.py`,
  `*.pb.go`, `*.g.dart`, `*.freezed.dart`, `*.generated.*`;
- **not binary** — no NUL byte in the first 4096 bytes;
- **not oversized** — at most 2 MB.

## Test-file detection

A file counts toward the numerator when its path matches either rule:

- **directory marker** — any parent path segment (case-insensitive, whole
  segment) from a broad multi-language set: `test(s)`, `spec(s)`,
  `__tests__`, `__mocks__`, `testing`, `unittest(s)`, `unit_test(s)`,
  `testcase(s)`, `e2e`, `integration_test(s)`, `itest(s)`,
  `functional_test(s)`, `acceptance*`, `smoke*`, `cypress`, `features`,
  `androidTest*`, `testsuite(s)`, …;
- **naming convention** — `test`/`spec` prefix or suffix separated by a
  delimiter (`test_x.py`, `x_test.go`, `x.spec.ts`), `conftest.py`,
  `*.feature`, or a CamelCase class suffix before a code extension
  (`FooTest.java`, `BarTests.cs`, `BazSpec.scala`, `QuxSuite.scala`,
  `AppTestCase.py`, `FooIT.java` — case-sensitive, so `latest.js` and
  `contest/` do not match).

## Zero semantics

`0` when the repository has no code lines at all (or no tests).  An actual
computation error leaves the cell empty (retried on the next run) and logs a
warning naming the repository and the metric.

## VCS support

Identical for Git and Mercurial (the ratio is computed over the tracked-file
list, which both backends provide in full).  Plain directories use the
filesystem fallback.
