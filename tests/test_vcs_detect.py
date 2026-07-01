"""Unit tests for VCS auto-detection (no hg/git binary required)."""

from __future__ import annotations

import pytest

from repo_metadata_cli.vcs.detect import (
    detect_vcs_from_path,
    detect_vcs_from_url,
    get_vcs,
    strip_vcs_scheme,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/org/repo.git",
        "https://github.com/org/repo",
        "git@gitlab.com:group/repo.git",
        "https://gitlab.com/group/sub/repo.git",
        "https://git.example.ru/team/service.git",
        # "hg" appearing in a GitHub path must NOT trigger Mercurial.
        "https://github.com/hgtools/repo",
        # Explicit git+ scheme overrides an hg host.
        "git+https://hg.mozilla.org/mozilla-central",
    ],
)
def test_url_detected_as_git(url):
    assert detect_vcs_from_url(url).name == "git"


@pytest.mark.parametrize(
    "url",
    [
        "https://hg.mozilla.org/mozilla-central",
        "https://hg.python.org/cpython",
        "https://foss.heptapod.net/group/repo",
        "ssh://hg@hg.example.org/repo",
        "https://www.mercurial-scm.org/repo",
        # Explicit hg+ scheme overrides a GitHub host.
        "hg+https://github.com/org/repo",
    ],
)
def test_url_detected_as_hg(url):
    assert detect_vcs_from_url(url).name == "hg"


def test_scheme_prefix_beats_heuristics_both_directions():
    assert detect_vcs_from_url("git+https://hg.mozilla.org/x").name == "git"
    assert detect_vcs_from_url("hg+https://github.com/o/r").name == "hg"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("hg+https://hg.example.org/r", "https://hg.example.org/r"),
        ("git+ssh://git@host/r", "ssh://git@host/r"),
        ("https://github.com/o/r.git", "https://github.com/o/r.git"),  # unchanged
        ("HG+https://x", "https://x"),  # case-insensitive prefix
    ],
)
def test_strip_vcs_scheme(raw, expected):
    assert strip_vcs_scheme(raw) == expected


def test_detect_from_path_hg(tmp_path):
    (tmp_path / ".hg").mkdir()
    assert detect_vcs_from_path(tmp_path).name == "hg"


def test_detect_from_path_git(tmp_path):
    (tmp_path / ".git").mkdir()
    assert detect_vcs_from_path(tmp_path).name == "git"


def test_detect_from_path_default_git(tmp_path):
    # A plain directory with neither .git nor .hg defaults to git.
    assert detect_vcs_from_path(tmp_path).name == "git"


def test_get_vcs_by_name():
    assert get_vcs("git").name == "git"
    assert get_vcs("hg").name == "hg"
    assert get_vcs("unknown").name == "git"  # default
