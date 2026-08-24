"""Unit tests for fetch URL partitioning (git → bash script, hg → Python path).

The bash script and hg subprocesses are monkeypatched, so no git/hg binary is
exercised here — only the routing logic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repo_metadata_cli import fetcher
from repo_metadata_cli.vcs import hg_fetch


def _write_repos(tmp_path: Path, lines) -> Path:
    f = tmp_path / "repos.txt"
    f.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return f


def _dirs(tmp_path):
    return {
        "bundles_dir": tmp_path / "bundles",
        "mirrors_dir": tmp_path / "mirrors",
        "ok_file": tmp_path / "ok.txt",
    }


def test_mixed_urls_partitioned(tmp_path, monkeypatch):
    repos = _write_repos(tmp_path, [
        "# comment line",
        "https://github.com/org/g1.git",
        "https://hg.mozilla.org/proj",
        "git+https://github.com/org/g2",          # git, prefix must be stripped
        "hg+https://github.com/org/h1",           # hg via explicit prefix
        "",
    ])

    captured = {}

    def fake_run_git_script(repos_file, bundles_dir, mirrors_dir, ok_file, env):
        captured["git_content"] = Path(repos_file).read_text(encoding="utf-8")

    def fake_fetch_hg(urls, **kwargs):
        captured["hg_urls"] = list(urls)

    monkeypatch.setattr(fetcher, "_run_git_script", fake_run_git_script)
    monkeypatch.setattr(fetcher, "fetch_hg_bundles", fake_fetch_hg)

    fetcher.fetch_bundles(repos, **_dirs(tmp_path))

    git_lines = [l for l in captured["git_content"].splitlines() if l.strip()]
    assert git_lines == [
        "https://github.com/org/g1.git",
        "https://github.com/org/g2",   # git+ prefix stripped
    ]
    assert captured["hg_urls"] == [
        "https://hg.mozilla.org/proj",
        "hg+https://github.com/org/h1",
    ]


def test_all_git_uses_original_file(tmp_path, monkeypatch):
    repos = _write_repos(tmp_path, [
        "https://github.com/org/g1.git",
        "https://gitlab.com/grp/g2.git",
    ])

    captured = {}

    def fake_run_git_script(repos_file, bundles_dir, mirrors_dir, ok_file, env):
        captured["path"] = Path(repos_file)

    def fake_fetch_hg(urls, **kwargs):
        captured["hg_called"] = True

    monkeypatch.setattr(fetcher, "_run_git_script", fake_run_git_script)
    monkeypatch.setattr(fetcher, "fetch_hg_bundles", fake_fetch_hg)

    fetcher.fetch_bundles(repos, **_dirs(tmp_path))

    # No hg URLs → the ORIGINAL repos file is handed to the script unchanged.
    assert captured["path"] == repos
    assert "hg_called" not in captured


def test_git_prefix_normalized_in_pure_git_file(tmp_path, monkeypatch):
    # A pure-git file containing a git+ prefix must be normalized (temp file),
    # not handed to the bash script verbatim — otherwise `git clone git+https://…` fails.
    repos = _write_repos(tmp_path, [
        "git+https://github.com/org/g1.git",
        "https://github.com/org/g2.git",
    ])

    captured = {}

    def fake_run_git_script(repos_file, bundles_dir, mirrors_dir, ok_file, env):
        captured["path"] = Path(repos_file)
        captured["content"] = Path(repos_file).read_text(encoding="utf-8")

    monkeypatch.setattr(fetcher, "_run_git_script", fake_run_git_script)
    monkeypatch.setattr(fetcher, "fetch_hg_bundles", lambda *a, **k: None)

    fetcher.fetch_bundles(repos, **_dirs(tmp_path))

    assert captured["path"] != repos  # a normalized temp file, not the original
    lines = [l for l in captured["content"].splitlines() if l.strip()]
    assert lines == [
        "https://github.com/org/g1.git",  # git+ stripped
        "https://github.com/org/g2.git",
    ]


def test_crlf_file_normalized_in_pure_git_file(tmp_path, monkeypatch):
    # A pure-git file with Windows line endings must be normalized (temp file):
    # the bash script reads lines raw, and a trailing \r reaches git, which
    # fails with "URL rejected: Malformed input to a URL function".
    repos = tmp_path / "repos.txt"
    repos.write_bytes(
        b"https://github.com/org/g1.git\r\n"
        b"https://gitlab.example.com/grp/g2.git\r\n"
    )

    captured = {}

    def fake_run_git_script(repos_file, bundles_dir, mirrors_dir, ok_file, env):
        captured["path"] = Path(repos_file)
        captured["content"] = Path(repos_file).read_text(encoding="utf-8")

    monkeypatch.setattr(fetcher, "_run_git_script", fake_run_git_script)
    monkeypatch.setattr(fetcher, "fetch_hg_bundles", lambda *a, **k: None)

    fetcher.fetch_bundles(repos, **_dirs(tmp_path))

    assert captured["path"] != repos  # a normalized temp file, not the original
    assert "\r" not in captured["content"]
    assert captured["content"].splitlines() == [
        "https://github.com/org/g1.git",
        "https://gitlab.example.com/grp/g2.git",
    ]


def test_padded_urls_normalized_in_pure_git_file(tmp_path, monkeypatch):
    # Leading/trailing whitespace around a URL must not reach the bash script.
    repos = _write_repos(tmp_path, [
        "  https://github.com/org/g1.git  ",
        "https://github.com/org/g2.git",
    ])

    captured = {}

    def fake_run_git_script(repos_file, bundles_dir, mirrors_dir, ok_file, env):
        captured["path"] = Path(repos_file)
        captured["content"] = Path(repos_file).read_text(encoding="utf-8")

    monkeypatch.setattr(fetcher, "_run_git_script", fake_run_git_script)
    monkeypatch.setattr(fetcher, "fetch_hg_bundles", lambda *a, **k: None)

    fetcher.fetch_bundles(repos, **_dirs(tmp_path))

    assert captured["path"] != repos
    assert captured["content"].splitlines() == [
        "https://github.com/org/g1.git",
        "https://github.com/org/g2.git",
    ]


def test_only_hg_does_not_invoke_git_script(tmp_path, monkeypatch):
    repos = _write_repos(tmp_path, ["https://hg.mozilla.org/proj"])

    captured = {"git_called": False}

    def fake_run_git_script(*a, **k):
        captured["git_called"] = True

    def fake_fetch_hg(urls, **kwargs):
        captured["hg_urls"] = list(urls)

    monkeypatch.setattr(fetcher, "_run_git_script", fake_run_git_script)
    monkeypatch.setattr(fetcher, "fetch_hg_bundles", fake_fetch_hg)

    fetcher.fetch_bundles(repos, **_dirs(tmp_path))

    assert captured["git_called"] is False
    assert captured["hg_urls"] == ["https://hg.mozilla.org/proj"]


def test_fetch_hg_bundles_skips_when_hg_absent(tmp_path, monkeypatch, caplog):
    # When hg is not installed, fetch_hg_bundles warns and creates no bundle.
    monkeypatch.setattr(hg_fetch.shutil, "which", lambda _: None)

    dirs = _dirs(tmp_path)
    with caplog.at_level("WARNING"):
        hg_fetch.fetch_hg_bundles(
            urls=["https://hg.mozilla.org/proj"],
            bundles_dir=dirs["bundles_dir"],
            mirrors_dir=dirs["mirrors_dir"],
            ok_file=dirs["ok_file"],
        )

    assert not list(dirs["bundles_dir"].glob("*.hgbundle")) if dirs["bundles_dir"].exists() else True
    assert any("hg" in r.message.lower() for r in caplog.records)
