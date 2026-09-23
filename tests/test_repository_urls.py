"""Repository location fields across source lists, local remotes and CSV resume."""

from pathlib import Path
import subprocess

import pandas as pd
import pytest
from typer.testing import CliRunner

from repo_metadata_cli import cli
from repo_metadata_cli.allowed_files import AllowedFiles
from repo_metadata_cli.base_metric import RepoContext
from repo_metadata_cli.config import AllowedFilesConfig
from repo_metadata_cli.csv_migration import NEW_COLUMNS, migrate_csv_schema
from repo_metadata_cli.partner import (
    build_org_map,
    build_url_map,
    normalize_repo_url,
    parse_repo_org,
)
from repo_metadata_cli.pipeline import run_metadata_pipeline
from repo_metadata_cli.settings import AppSettings, load_app_settings


CONFIG = Path(__file__).resolve().parents[1] / "repo_metadata.toml"
SOURCE_URL = "https://username:TOKEN@git.example.com/group/service.git"
METADATA_URL = "https://git.example.com/group/service.git"


@pytest.mark.parametrize("source, expected", [
    (SOURCE_URL, METADATA_URL),
    ("http://username:TOKEN@git.example.com/group/service.git",
     "http://git.example.com/group/service.git"),
    ("https://TOKEN@git.example.com/group/service.git", METADATA_URL),
    ("https://user%40example.com:to%3Aken%40value@git.example.com:8443/group/service.git",
     "https://git.example.com:8443/group/service.git"),
    ("https://user:TOKEN@[2001:db8::1]:8443/group/service.git",
     "https://[2001:db8::1]:8443/group/service.git"),
    ("HTTPS://user:TOKEN@git.example.com/group/service.git?ref=main#section",
     "HTTPS://git.example.com/group/service.git?ref=main#section"),
    ("hg+" + SOURCE_URL, "hg+" + METADATA_URL),
    ("git+" + SOURCE_URL, "git+" + METADATA_URL),
    ("https://git.example.com/group/a@b.git?ref=a@b#c@d",
     "https://git.example.com/group/a@b.git?ref=a@b#c@d"),
    (METADATA_URL, METADATA_URL),
    ("git@git.example.com:group/service.git", "git@git.example.com:group/service.git"),
    ("ssh://git@git.example.com:10022/group/service.git",
     "ssh://git@git.example.com:10022/group/service.git"),
    ("/data/repos/user@host/service", "/data/repos/user@host/service"),
    ("hg+/data/repos/service", "hg+/data/repos/service"),
    ("", ""),
])
def test_metadata_url_format(source, expected):
    assert normalize_repo_url(source) == expected
    assert normalize_repo_url(expected) == expected


@pytest.mark.parametrize("source", [
    SOURCE_URL,
    "https://user:TOKEN@git.example.com:8443/group/service.git",
    "https://user%40example.com:TOKEN@[2001:db8::1]:8443/group/service.git",
    "hg+" + SOURCE_URL,
    "git+" + SOURCE_URL,
    "git@git.example.com:group/service.git",
    "ssh://git@git.example.com:10022/group/service.git",
])
def test_namespace_comes_from_repository_path(source):
    assert parse_repo_org(source) == "group"


def test_source_list_maps_preserve_the_fetch_input(tmp_path):
    source = tmp_path / "repos.txt"
    original = f"# example\n\n{SOURCE_URL}\n"
    source.write_text(original)
    assert build_url_map(source) == {"group-service": METADATA_URL}
    assert build_org_map(source) == {"group-service": "group"}
    assert source.read_text() == original


def test_context_normalizes_a_caller_supplied_url_map(tmp_path):
    settings = AppSettings()
    settings.url_map = {"group-service": SOURCE_URL}
    context = RepoContext(
        repo_path=tmp_path,
        bundle_path=tmp_path / "group-service.bundle",
        settings=settings,
        tree_sitter=None,
        allowed_files=None,
    )
    assert context.repo_url == METADATA_URL
    assert settings.url_map["group-service"] == SOURCE_URL


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _repository(path):
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    (path / "app.py").write_text("def increment(value):\n    return value + 1\n")
    _git(path, "add", ".")
    _git(path, "-c", "user.name=Example", "-c", "user.email=example@example.com",
         "-c", "commit.gpgsign=false", "commit", "-qm", "Initial example")
    _git(path, "remote", "add", "origin", SOURCE_URL)
    return path


def _run_pipeline(dataset, csv):
    return run_metadata_pipeline(
        dataset, csv, load_app_settings(CONFIG),
        AllowedFiles(AllowedFilesConfig(config_file=CONFIG)), None,
    )


def _read(csv):
    return pd.read_csv(csv, dtype=str, keep_default_na=False)


def test_local_remote_csv_uses_metadata_url_without_changing_remote(tmp_path):
    dataset = tmp_path / "sources"
    repo = _repository(dataset / "service")
    csv = tmp_path / "metadata.csv"
    _run_pipeline(dataset, csv)
    row = _read(csv).iloc[0]
    assert row["repo_url"] == METADATA_URL
    assert row["repo_org"] == "group"
    assert "TOKEN" not in csv.read_text()
    assert _git(repo, "remote", "get-url", "origin") == SOURCE_URL


def test_cli_source_list_exports_normalized_location(tmp_path, monkeypatch):
    repo = _repository(tmp_path / "source")
    repos = tmp_path / "repos.txt"
    repos.write_text(SOURCE_URL + "\n")
    csv = tmp_path / "metadata.csv"
    bundles = tmp_path / "bundles"

    def fetch_bundles(*, repos_file, bundles_dir, **kwargs):
        # Assert the fetch boundary still receives the original address.
        assert repos_file.read_text() == SOURCE_URL + "\n"
        bundles_dir.mkdir()
        _git(repo, "bundle", "create", str(bundles_dir / "group-service.bundle"), "--all")

    monkeypatch.setattr(cli, "fetch_bundles", fetch_bundles)
    monkeypatch.setattr(cli, "ensure_scc", lambda **kwargs: None)
    monkeypatch.setattr(cli, "ensure_jscpd", lambda **kwargs: None)
    result = CliRunner().invoke(cli.app, [
        "metadata", str(repos), "--output-csv", str(csv),
        "--bundles-dir", str(bundles), "--config-file", str(CONFIG),
        "--skip-tree-sitter",
    ], env={"GITLAB_TOKEN": "", "GITHUB_TOKEN": ""})
    assert result.exit_code == 0, result.output
    row = _read(csv).iloc[0]
    assert row["repo_url"] == METADATA_URL
    assert row["repo_org"] == "group"
    assert row["repo_name"] == "service"
    assert "TOKEN" not in csv.read_text()
    assert repos.read_text() == SOURCE_URL + "\n"


def _saved_row(**overrides):
    return {
        "repo_id": "example-id", "repo_name": "service",
        "repo_url": SOURCE_URL, "repo_org": "TOKEN@git.example.com/group",
        "logical_loc": "001.500", "custom_col": "None",
        **{column: "0" for column in NEW_COLUMNS}, **overrides,
    }


def test_saved_location_normalization_preserves_rows_and_other_fields(tmp_path):
    csv = tmp_path / "metadata.csv"
    rows = [
        _saved_row(),
        _saved_row(repo_id="other-id", repo_name="other", repo_url=METADATA_URL,
                   repo_org="custom-label"),
        _saved_row(repo_id="empty-id", repo_name="empty", repo_url="", repo_org=""),
    ]
    # Current files can have a custom column after the metric tail.
    columns = [key for key in rows[0] if key != "custom_col"] + ["custom_col"]
    pd.DataFrame(rows, columns=columns).to_csv(csv, index=False)
    assert migrate_csv_schema(csv) is True
    expected = [dict(rows[0], repo_url=METADATA_URL, repo_org="group"), *rows[1:]]
    assert _read(csv).to_dict("records") == expected
    assert _read(csv).columns.tolist() == columns
    first = csv.read_bytes()
    assert migrate_csv_schema(csv) is False
    assert csv.read_bytes() == first


def test_saved_url_is_normalized_without_a_namespace_column(tmp_path):
    csv = tmp_path / "metadata.csv"
    row = _saved_row()
    del row["repo_org"]
    pd.DataFrame([row]).to_csv(csv, index=False)
    assert migrate_csv_schema(csv)
    assert _read(csv).to_dict("records") == [dict(row, repo_url=METADATA_URL)]


def test_resume_normalizes_location_when_source_is_absent(tmp_path):
    dataset = tmp_path / "empty"
    dataset.mkdir()
    csv = tmp_path / "metadata.csv"
    pd.DataFrame([_saved_row()]).to_csv(csv, index=False)
    assert _run_pipeline(dataset, csv) == {"skipped": [], "degraded": []}
    assert _read(csv).to_dict("records") == [
        _saved_row(repo_url=METADATA_URL, repo_org="group")
    ]


def test_resume_normalizes_completed_row_without_recollecting(tmp_path, monkeypatch):
    dataset = tmp_path / "sources"
    _repository(dataset / "service")
    csv = tmp_path / "metadata.csv"
    pd.DataFrame([_saved_row()]).to_csv(csv, index=False)

    def unexpected_collection(*args, **kwargs):
        pytest.fail("A completed row should be reused")

    monkeypatch.setattr("repo_metadata_cli.pipeline.build_local_repo_context", unexpected_collection)
    _run_pipeline(dataset, csv)
    assert _read(csv).to_dict("records") == [
        _saved_row(repo_url=METADATA_URL, repo_org="group")
    ]
