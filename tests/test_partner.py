"""Unit tests for partner_name resolution from repos.txt URLs."""

from __future__ import annotations

from repo_metadata_cli.partner import (
    DEFAULT_PARTNER,
    bundle_stem_from_url,
    build_partner_map,
    parse_partner_name,
)

_PARTNER_URL = (
    "https://gitlab.com/doubletapp/data-llm/partner-private-repos/acme_corp/cool-repo.git"
)


def test_parse_partner_name_matches_pattern():
    assert parse_partner_name(_PARTNER_URL) == "acme_corp"


def test_parse_partner_name_no_match_returns_none():
    assert parse_partner_name("https://github.com/org/repo.git") is None
    assert parse_partner_name("https://gitlab.com/group/sub/repo.git") is None


def test_bundle_stem_matches_repo_basename():
    assert bundle_stem_from_url(_PARTNER_URL) == "cool-repo"
    # Non-alphanumeric chars (e.g. underscore) are sanitized to '-', matching
    # fetch_bundles.sh repo_only_name.
    assert bundle_stem_from_url("https://github.com/org/My_Repo.git") == "My-Repo"
    assert bundle_stem_from_url("git@gitlab.com:group/repo.git") == "repo"


def test_build_partner_map(tmp_path):
    repos = tmp_path / "repos.txt"
    repos.write_text(
        "\n".join([
            "# comment line",
            "",
            _PARTNER_URL,
            "https://gitlab.com/doubletapp/data-llm/partner-private-repos/beta-team/svc.git",
            "https://github.com/external/other.git",
        ])
    )
    mapping = build_partner_map(repos)
    assert mapping["cool-repo"] == "acme_corp"
    assert mapping["svc"] == "beta-team"
    # URL outside the partner pattern falls back to "bundles".
    assert mapping["other"] == DEFAULT_PARTNER == "bundles"


def test_build_partner_map_missing_file(tmp_path):
    assert build_partner_map(tmp_path / "nope.txt") == {}


# ---------------------------------------------------------------------------
# RepoContext.partner_name wiring
# ---------------------------------------------------------------------------

from pathlib import Path  # noqa: E402

from repo_metadata_cli.base_metric import RepoContext  # noqa: E402
from repo_metadata_cli.settings import AppSettings  # noqa: E402


def _ctx(bundle_path, partner_map):
    settings = AppSettings()
    settings.partner_map = partner_map
    return RepoContext(
        repo_path=Path("/tmp/work/cool-repo"),
        settings=settings,
        tree_sitter=None,
        allowed_files=None,  # not used by partner_name
        bundle_path=bundle_path,
    )


def test_partner_name_from_map():
    ctx = _ctx(Path("/tmp/bundles/cool-repo.bundle"), {"cool-repo": "acme_corp"})
    assert ctx.partner_name == "acme_corp"


def test_partner_name_default_bundles_when_no_match():
    ctx = _ctx(Path("/tmp/bundles/cool-repo.bundle"), {"cool-repo": "bundles"})
    assert ctx.partner_name == "bundles"


def test_partner_name_falls_back_to_dir_when_map_empty():
    # No repos.txt mode → empty map → previous behaviour (parent dir name).
    ctx = _ctx(Path("/tmp/bundles/cool-repo.bundle"), {})
    assert ctx.partner_name == "bundles"
