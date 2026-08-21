"""Exact command recipes and parsers for the meta_* comparison metrics."""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace

import pytest

from repo_metadata_cli.metrics import external


def test_parse_meta_logical_loc_sums_language_code() -> None:
    output = """[
        {"Name": "Python", "Code": 17},
        {"Name": "JSON", "Code": 8},
        {"Name": "empty"},
        {"Name": "bad", "Code": "unknown"}
    ]"""
    assert external.parse_meta_logical_loc(output) == 25
    assert external.parse_meta_logical_loc("not json") == 0


def test_parse_meta_non_authored_loc_selects_generated_true() -> None:
    output = """[
        {"Name": "Python", "Files": [
            {"Location": "generated.py", "Generated": true, "Code": 11},
            {"Location": "authored.py", "Generated": false, "Code": 7},
            {"Location": "string.py", "Generated": "true", "Code": 100}
        ]},
        {"Name": "Go", "Files": [
            {"Location": "model.pb.go", "Generated": true, "Code": 13}
        ]}
    ]"""
    assert external.parse_meta_non_authored_loc(output) == 24
    assert external.parse_meta_non_authored_loc("{}") == 0


def test_parse_meta_duplication_ratio_reads_total_percentage() -> None:
    output = '{"statistics":{"total":{"percentage":12.75}}}'
    assert external.parse_meta_duplication_ratio(output) == 0.1275
    assert external.parse_meta_duplication_ratio("[]") == 0.0


def test_parse_meta_non_merge_commit_count_filters_revert_case_insensitively() -> None:
    output = """a1 First commit
b2 Revert first commit
c3 Add feature
d4 REVERT "Add feature"
e5 never reverting this text"""
    assert external.parse_meta_non_merge_commit_count(output) == 2


def test_meta_logical_loc_uses_exact_scc_command(monkeypatch, tmp_path: Path) -> None:
    calls = []

    def fake_run(command, repo_path, metric_name):
        calls.append((command, repo_path, metric_name))
        return '[{"Name":"Python","Code":9}]'

    monkeypatch.setattr(external, "_run_stdout", fake_run)
    assert external.get_meta_logical_loc(tmp_path) == 9
    assert calls == [(["scc", ".", "--format", "json"], tmp_path, "meta_logical_loc")]


def test_meta_non_authored_loc_uses_exact_scc_command(
    monkeypatch, tmp_path: Path
) -> None:
    calls = []

    def fake_run(command, repo_path, metric_name):
        calls.append((command, repo_path, metric_name))
        return '[{"Name":"Go","Files":[{"Generated":true,"Code":5}]}]'

    monkeypatch.setattr(external, "_run_stdout", fake_run)
    assert external.get_meta_non_authored_loc(tmp_path) == 5
    assert calls == [
        (
            [
                "scc",
                ".",
                "--gen",
                "--by-file",
                "--exclude-dir",
                "vendor,node_modules,dist,build,generated,migrations",
                "--format",
                "json",
            ],
            tmp_path,
            "meta_non_authored_loc",
        )
    ]


def test_meta_duplication_ratio_uses_exact_jscpd_flags(
    monkeypatch, tmp_path: Path
) -> None:
    calls = []

    def fake_run(command, repo_path, metric_name):
        calls.append((command, repo_path, metric_name))
        report_dir = Path(command[-1])
        (report_dir / "jscpd-report.json").write_text(
            '{"statistics":{"total":{"percentage":33.3}}}',
            encoding="utf-8",
        )
        return ""

    monkeypatch.setattr(external, "_run_stdout", fake_run)
    assert external.get_meta_duplication_ratio(tmp_path) == pytest.approx(0.333)
    command, repo_path, metric_name = calls[0]
    assert command[:-2] == [
        "jscpd",
        ".",
        "--min-tokens",
        "50",
        "--min-lines",
        "5",
        "--reporters",
        "json",
    ]
    assert command[-2] == "--output"
    assert repo_path == tmp_path
    assert metric_name == "meta_duplication_ratio"


def test_meta_non_merge_commit_count_uses_head_without_all(
    monkeypatch, tmp_path: Path
) -> None:
    calls = []

    def fake_run(command, repo_path, metric_name):
        calls.append((command, repo_path, metric_name))
        return "a1 first\nb2 revert old\nc3 third\n"

    monkeypatch.setattr(external, "_run_stdout", fake_run)
    assert external.get_meta_non_merge_commit_count(tmp_path) == 2
    assert calls == [
        (
            ["git", "log", "--oneline", "--no-merges"],
            tmp_path,
            "meta_non_merge_commit_count",
        )
    ]


def test_meta_non_merge_commit_count_is_zero_for_mercurial(monkeypatch) -> None:
    def fail_if_called(repo_path):
        raise AssertionError(f"git recipe called for Mercurial repo {repo_path}")

    monkeypatch.setattr(external, "get_meta_non_merge_commit_count", fail_if_called)
    ctx = SimpleNamespace(vcs=SimpleNamespace(name="hg"))
    assert external.MetaNonMergeCommitCountMetric().compute(ctx) == 0


def test_missing_external_tool_logs_warning(
    monkeypatch, tmp_path: Path, caplog
) -> None:
    def missing(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(external.subprocess, "run", missing)
    with caplog.at_level(logging.WARNING):
        assert external._run_stdout(["scc", "."], tmp_path, "meta_logical_loc") == ""
    assert "scc is not installed" in caplog.text
    assert "meta_logical_loc" in caplog.text
