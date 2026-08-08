# Metrics `functions_count` (column BC), `classes_count` (column BD)

Counts of function/method definitions and class-like type declarations,
computed with tree-sitter.  Deterministic — no LLM, no network calls.
Both are 0 when the pipeline runs with `--skip-tree-sitter`.

## Shared AST pass

One traversal per file serves `avg_func_length` (AA), `functions_count` (BC)
and `classes_count` (BD); the result is cached on the repo context
(`func_length_stats`).

File selection: every parseable code file of the worktree EXCEPT vendor,
dependency and build directories — the same `scc_exclude_dirs` list that
scopes `logical_loc` (plus `.git`/`.hg`/`.svn` always; exact path-segment
match at any depth) — and EXCEPT generated files (`*.min.js`, `*.d.ts`,
`*_pb2.py`, `*.pb.go`, `*.g.dart`, `*.freezed.dart`, `*.generated.*`, …) and
files over 2 MB, the same rules the test estimates (BB/BE) use.  So one
committed bundle or a codegen twin per model does not dominate the counts.

## functions_count

A node counts when its type is listed in `[tree_sitter.lang_func_node_types]`
for the file's language — the exact node-type sets `avg_func_length` has
always used (functions, methods, constructors where configured).  Known
per-language quirks are inherited: js/ts count every `arrow_function`
(inline callbacks included), Julia short-form `f(x) = ...` definitions are
not counted, bodyless signatures (interfaces, abstract methods) are not
counted in most languages.

## classes_count

A node counts when its type is listed in
`[tree_sitter.lang_class_node_types]` (every entry verified empirically
against the pinned tree-sitter-language-pack grammars).  Counted are named
type declarations with OOP flavor: classes, interfaces, traits, protocols,
objects, records — plus structs where structs are the class analog (C++, C#,
Swift, Rust, Go, Zig, Odin, Nim, Julia).  Type aliases and methodless C-style
enums are excluded wherever the grammar distinguishes them.

Structural filters on top of the node-type match
(`metric_utils._is_class_definition_node`):

- only NAMED nodes count — in ruby/js/ts the `class` keyword token shares the
  type string with the declaration node;
- C-family `class_specifier`/`struct_specifier` (cpp, glsl, hlsl) must carry
  a body (`field_declaration_list`) — forward declarations (`class Fwd;`) and
  elaborated type references (`struct Point p;`) do not count;
- Go `type_spec` must contain a `struct_type`/`interface_type` child — named
  scalar types (`type MyID int`) and aliases do not count;
- Swift `extension` declarations share `class_declaration` with
  class/struct/enum/actor and are skipped (they extend an existing type).

Accepted quirks (grammar cannot distinguish): Kotlin interfaces and Swift/
Scala/Java/PHP/Dart enums WITH methods share the class node type, so
methodless variants of those constructs count too; Ruby counts each reopening
of a class; Objective-C counts `@interface` and `@implementation` of the same
class separately; a language absent from the table (elixir, lua, r, bash, …)
contributes 0.  Pure-C repos are a special case: C itself is configured with
no class nodes, but `.h` files are mapped to the cpp grammar (pre-existing
extension mapping), so struct definitions living in headers DO count while
identical structs in `.c` files do not — on C repos read the value as
"header-declared structs", not classes.

## Zero semantics

`0` when tree-sitter is skipped, the language has no configured node types,
or the repo has no matching constructs.  A grammar that fails to LOAD is
logged as a warning and silently contributes 0 for its files — the metric
does not error.  A computation error leaves the cell empty (retried on the
next run).
