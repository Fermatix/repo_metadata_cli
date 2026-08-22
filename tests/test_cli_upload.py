from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from repo_metadata_cli import cli
from repo_metadata_cli.crm_upload import CRMUploadError

runner = CliRunner()


def _patch_pipeline(monkeypatch, output_csv, summary, events):
    tree_sitter = SimpleNamespace(
        extension_language_map={},
        lang_func_node_types={},
        lang_class_node_types={},
        language_packages={},
    )
    settings = SimpleNamespace(tree_sitter=tree_sitter)

    monkeypatch.setattr(cli, "ensure_scc", lambda **kwargs: None)
    monkeypatch.setattr(cli, "ensure_hg", lambda *args, **kwargs: None)
    monkeypatch.setattr(cli, "load_app_settings", lambda path: settings)
    monkeypatch.setattr(cli, "_build_allowed_files", lambda path: object())
    monkeypatch.setattr(cli, "_build_ts_manager", lambda *args, **kwargs: None)

    def run_pipeline(**kwargs):
        events.append("pipeline")
        assert kwargs["csv_path"] == output_csv
        output_csv.write_text("repo_id,partner_name\n1,partner\n", encoding="utf-8")
        return summary

    monkeypatch.setattr(cli, "run_metadata_pipeline", run_pipeline)


def test_upload_runs_before_partial_result_exit(tmp_path, monkeypatch):
    dataset = tmp_path / "bundles"
    dataset.mkdir()
    output_csv = tmp_path / "result.csv"
    events = []
    _patch_pipeline(monkeypatch, output_csv, {"skipped": ["repository-b"]}, events)

    def fake_upload(**kwargs):
        events.append("upload")
        assert kwargs == {
            "csv_path": output_csv,
            "login": "login",
            "password": "secret",
            "crm_url": "https://crm.example/",
        }
        return {
            "success_count": 1,
            "created_count": 1,
            "updated_count": 0,
            "errors": [],
            "total_rows": 1,
        }

    monkeypatch.setattr(cli, "upload_csv", fake_upload)

    result = runner.invoke(
        cli.app,
        [
            "metadata",
            str(dataset),
            "--output-csv",
            str(output_csv),
            "--skip-tree-sitter",
            "--upload",
            "--crm-url",
            "https://crm.example/",
        ],
        env={"CRM_LOGIN": "login", "CRM_PASSWORD": "secret"},
    )

    assert result.exit_code == 1
    assert events == ["pipeline", "upload"]
    assert "CRM import: created=1, updated=0, errors=0." in result.output
    assert (
        "WARNING: 1 repository/repositories produced no row: repository-b"
        in result.output
    )


def test_upload_requires_credentials_before_pipeline(tmp_path, monkeypatch):
    dataset = tmp_path / "bundles"
    dataset.mkdir()
    monkeypatch.setattr(
        cli,
        "ensure_scc",
        lambda **kwargs: pytest.fail("pipeline preparation must not start"),
    )

    result = runner.invoke(
        cli.app,
        ["metadata", str(dataset), "--upload"],
        env={"CRM_PASSWORD": "secret"},
    )

    assert result.exit_code == 2
    assert "--login" in result.output
    assert "required with --upload" in result.output


def test_upload_failure_returns_nonzero_exit(tmp_path, monkeypatch):
    dataset = tmp_path / "bundles"
    dataset.mkdir()
    output_csv = tmp_path / "result.csv"
    events = []
    _patch_pipeline(monkeypatch, output_csv, {"skipped": []}, events)
    monkeypatch.setattr(
        cli,
        "upload_csv",
        lambda **kwargs: (_ for _ in ()).throw(CRMUploadError("service unavailable")),
    )

    result = runner.invoke(
        cli.app,
        [
            "metadata",
            str(dataset),
            "--output-csv",
            str(output_csv),
            "--skip-tree-sitter",
            "--upload",
        ],
        env={"CRM_LOGIN": "login", "CRM_PASSWORD": "secret"},
    )

    assert result.exit_code == 1
    assert "ERROR: service unavailable" in result.output
