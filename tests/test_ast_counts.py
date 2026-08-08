"""Tree-sitter function/class counting (functions_count BC, classes_count BD).

Covers the shared AST pass (FunctionLengthStats), the vendor-dir exclusion of
iter_code_files, and the structural filters for grammars whose class node type
over-matches (cpp forward declarations, Go type_spec, Swift extensions,
ruby/js keyword tokens sharing the declaration node type).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_metadata_cli.allowed_files import AllowedFiles
from repo_metadata_cli.base_metric import RepoContext
from repo_metadata_cli.config import AllowedFilesConfig, TreeSitterConfig
from repo_metadata_cli.metric_utils import (
    compute_avg_func_length_stats,
    iter_code_files,
)
from repo_metadata_cli.metrics.docs import (
    AvgFuncLengthMetric,
    ClassesCountMetric,
    FunctionsCountMetric,
)
from repo_metadata_cli.settings import load_app_settings
from repo_metadata_cli.tree_sitter_support import TreeSitterManager

_PROJECT_ROOT = Path(__file__).parent.parent
_TOML = _PROJECT_ROOT / "repo_metadata.toml"

_SETTINGS = load_app_settings(_TOML)


def _ts_manager() -> TreeSitterManager:
    return TreeSitterManager(
        TreeSitterConfig(
            extension_language_map=_SETTINGS.tree_sitter.extension_language_map,
            lang_func_node_types=_SETTINGS.tree_sitter.lang_func_node_types,
            lang_class_node_types=_SETTINGS.tree_sitter.lang_class_node_types,
        )
    )


def _allowed() -> AllowedFiles:
    return AllowedFiles(AllowedFilesConfig(config_file=_TOML))


def _ctx(repo: Path, tree_sitter=None) -> RepoContext:
    return RepoContext(
        repo_path=repo,
        settings=_SETTINGS,
        tree_sitter=tree_sitter,
        allowed_files=_allowed(),
        vcs=None,
    )


def _stats(repo: Path):
    return compute_avg_func_length_stats(
        repo, _allowed(), _ts_manager(),
        exclude_dirs=list(_SETTINGS.metrics.scc_exclude_dirs),
    )


def _write(repo: Path, rel: str, content: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content)


# --- per-language class counting ---------------------------------------------

def test_python_classes_and_functions(tmp_path):
    _write(tmp_path, "app.py", (
        "class A:\n"
        "    def m(self):\n"
        "        pass\n"
        "\n"
        "class B:\n"
        "    pass\n"
        "\n"
        "def f():\n"
        "    pass\n"
    ))
    stats = _stats(tmp_path)
    assert stats.class_count == 2
    assert stats.function_count == 2  # m + f


def test_ruby_keyword_token_not_double_counted(tmp_path):
    _write(tmp_path, "app.rb", (
        "class K\n"
        "  def m; end\n"
        "end\n"
        "module M\n"
        "end\n"
    ))
    # the `class`/`module` KEYWORD tokens share the node type string with the
    # declaration; only named nodes may count
    assert _stats(tmp_path).class_count == 2


def test_typescript_variants(tmp_path):
    _write(tmp_path, "app.ts", (
        "class T {}\n"
        "abstract class AT {}\n"
        "interface IT { m(): void }\n"
        "const E = class {};\n"
        "enum En { X }\n"          # TS enums cannot hold methods: excluded
        "type Alias = string;\n"   # alias: excluded
    ))
    assert _stats(tmp_path).class_count == 4


def test_cpp_forward_declarations_excluded(tmp_path):
    _write(tmp_path, "x.cpp", (
        "class Fwd;\n"                    # forward declaration: no body
        "class Real { int x; };\n"
        "struct Pt { int x; };\n"
        "void takes(struct Pt p);\n"      # elaborated type reference
    ))
    assert _stats(tmp_path).class_count == 2


def test_go_only_structs_and_interfaces(tmp_path):
    _write(tmp_path, "main.go", (
        "package main\n"
        "type S struct{ x int }\n"
        "type I interface{ M() }\n"
        "type MyID int\n"          # named non-OOP type: excluded
        "type Alias = int\n"       # alias: excluded
        "func f() {}\n"
    ))
    stats = _stats(tmp_path)
    assert stats.class_count == 2
    assert stats.function_count == 1


def test_swift_extensions_excluded(tmp_path):
    _write(tmp_path, "x.swift", (
        "class C {}\n"
        "struct S {}\n"
        "protocol P {}\n"
        "extension C { func e() {} }\n"  # extends an existing type
    ))
    assert _stats(tmp_path).class_count == 3


def test_java_type_declarations(tmp_path):
    _write(tmp_path, "A.java", (
        "class A { void m() {} }\n"
        "interface I {}\n"
        "enum E { X }\n"
        "record R(int x) {}\n"
    ))
    assert _stats(tmp_path).class_count == 4


# --- vendor exclusion and plumbing --------------------------------------------

def test_vendor_dirs_excluded_from_ast_metrics(tmp_path):
    _write(tmp_path, "src/app.py", "class A:\n    pass\n\ndef f():\n    pass\n")
    _write(tmp_path, "node_modules/lib/v.py", "class V:\n    pass\n\ndef vf():\n    pass\n")
    _write(tmp_path, "vendor/pkg/w.py", "class W:\n    pass\n")
    stats = _stats(tmp_path)
    assert stats.class_count == 1
    assert stats.function_count == 1


def test_iter_code_files_exclude_dirs_segment_match(tmp_path):
    _write(tmp_path, "src/app.py", "x = 1\n")
    _write(tmp_path, "node_modules/l.py", "x = 1\n")
    _write(tmp_path, "src/node_modules_like.py", "x = 1\n")  # not a dir segment
    got = {
        p.relative_to(tmp_path).as_posix()
        for p in iter_code_files(tmp_path, _allowed(), ["node_modules"])
    }
    assert got == {"src/app.py", "src/node_modules_like.py"}


def test_generated_and_oversized_files_excluded_from_ast_metrics(tmp_path):
    # same generated-file rules as the test estimates (BB/BE/BF): codegen
    # twins and minified bundles must not dominate the counts
    _write(tmp_path, "src/user.dart", "class User { void m() {} }\n")
    _write(tmp_path, "src/user.g.dart", "class _$UserImpl { void m() {} }\n" * 5)
    _write(tmp_path, "src/schema.d.ts", "interface A {}\n" * 50)
    _write(tmp_path, "src/bundle.min.js", "const f = () => 1;\n" * 100)
    _write(tmp_path, "src/big.py", "def f():\n    pass\n" * 400_000)  # > 2 MB
    stats = _stats(tmp_path)
    assert stats.class_count == 1
    assert stats.function_count == 1


def test_docstring_ratio_excludes_vendor_dirs(tmp_path):
    from repo_metadata_cli.metric_utils import compute_docstring_ratio
    _write(tmp_path, "src/app.py", 'def f():\n    """doc"""\n    pass\n')
    # vendored: two undocumented functions would drag the ratio to 1/3
    _write(tmp_path, "node_modules/v.py", "def a():\n    pass\n\ndef b():\n    pass\n")
    ratio = compute_docstring_ratio(
        tmp_path, _allowed(), _ts_manager(),
        exclude_dirs=list(_SETTINGS.metrics.scc_exclude_dirs),
    )
    assert ratio == 1.0


def test_metrics_share_one_cached_pass(tmp_path):
    _write(tmp_path, "app.py", "class A:\n    def m(self):\n        pass\n")
    ctx = _ctx(tmp_path, tree_sitter=_ts_manager())
    assert FunctionsCountMetric().compute(ctx) == 1
    assert ClassesCountMetric().compute(ctx) == 1
    assert AvgFuncLengthMetric().compute(ctx) == 2.0
    assert "func_length_stats" in ctx._cache


def test_zero_without_tree_sitter(tmp_path):
    _write(tmp_path, "app.py", "class A:\n    pass\n")
    ctx = _ctx(tmp_path, tree_sitter=None)
    assert FunctionsCountMetric().compute(ctx) == 0
    assert ClassesCountMetric().compute(ctx) == 0


def test_toml_without_class_table_is_valid(tmp_path):
    # lang_class_node_types is OPTIONAL: a legacy TOML must load fine and
    # classes_count must simply stay 0.
    minimal = tmp_path / "cfg.toml"
    minimal.write_text(
        "[tree_sitter.extension_language_map]\n"
        '".py" = "python"\n'
        "[tree_sitter.lang_func_node_types]\n"
        'python = ["function_definition"]\n'
    )
    settings = load_app_settings(minimal)
    assert settings.tree_sitter.lang_class_node_types == {}
    ts = TreeSitterManager(
        TreeSitterConfig(
            extension_language_map=settings.tree_sitter.extension_language_map,
            lang_func_node_types=settings.tree_sitter.lang_func_node_types,
            lang_class_node_types=settings.tree_sitter.lang_class_node_types,
        )
    )
    _write(tmp_path, "app.py", "class A:\n    def m(self):\n        pass\n")
    stats = compute_avg_func_length_stats(tmp_path, _allowed(), ts)
    assert stats.function_count == 1
    assert stats.class_count == 0


def test_class_only_language_config_still_parses(tmp_path):
    # a language configured with classes but NO functions must not be skipped
    cfg = TreeSitterConfig(
        extension_language_map={".py": "python"},
        lang_func_node_types={},
        lang_class_node_types={"python": {"class_definition"}},
    )
    ts = TreeSitterManager(cfg)
    _write(tmp_path, "app.py", "class A:\n    pass\n")
    stats = compute_avg_func_length_stats(tmp_path, _allowed(), ts)
    assert stats.class_count == 1
    assert stats.function_count == 0
