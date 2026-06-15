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


def test_bundle_stem_is_full_path():
    # Bundles are now named by the FULL namespace path (safe_name), so the same
    # leaf under different groups yields distinct stems (no collision).
    assert bundle_stem_from_url(_PARTNER_URL) == (
        "doubletapp-data-llm-partner-private-repos-acme-corp-cool-repo"
    )
    assert (
        bundle_stem_from_url("https://git.softlex.pro/softlex/prima-finance/backend.git")
        != bundle_stem_from_url("https://git.softlex.pro/softlex/ultima-finance/backend.git")
    )
    assert bundle_stem_from_url("https://github.com/org/My_Repo.git") == "org-My-Repo"
    assert bundle_stem_from_url("git@gitlab.com:group/repo.git") == "group-repo"


def test_repo_leaf_from_url():
    from repo_metadata_cli.partner import repo_leaf_from_url
    assert repo_leaf_from_url(_PARTNER_URL) == "cool-repo"
    assert repo_leaf_from_url(
        "https://git.softlex.pro/softlex/prima-finance/backend.git"
    ) == "backend"
    assert repo_leaf_from_url("git@github.com:owner/Repo.Name.git") == "Repo.Name"


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
    assert mapping[bundle_stem_from_url(_PARTNER_URL)] == "acme_corp"
    assert mapping["doubletapp-data-llm-partner-private-repos-beta-team-svc"] == "beta-team"
    # URL outside the partner pattern falls back to "bundles".
    assert mapping["external-other"] == DEFAULT_PARTNER == "bundles"


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


# --- repo_org -------------------------------------------------------------

def test_parse_repo_org_full_namespace():
    from repo_metadata_cli.partner import parse_repo_org
    assert parse_repo_org(
        "https://git.softlex.pro/softlex/funeral-organizer/devops/logging.git"
    ) == "softlex/funeral-organizer/devops"
    assert parse_repo_org(_PARTNER_URL) == (
        "doubletapp/data-llm/partner-private-repos/acme_corp"
    )
    assert parse_repo_org("git@github.com:owner/Repo.Name.git") == "owner"


def test_parse_repo_org_none_when_no_namespace():
    from repo_metadata_cli.partner import parse_repo_org
    assert parse_repo_org("https://example.com/justrepo.git") is None
    assert parse_repo_org("") is None


def test_build_org_map_no_collision_full_stem(tmp_path):
    from repo_metadata_cli.partner import build_org_map, bundle_stem_from_url
    f = tmp_path / "repos.txt"
    urls = [
        "https://git.softlex.pro/softlex/prima-finance/backend.git",
        "https://git.softlex.pro/softlex/ultima-finance/backend.git",
        "https://git.softlex.pro/softlex/atom/atom-backend.git",
    ]
    f.write_text("\n".join(urls) + "\n")
    m = build_org_map(f)
    # Full-path stems are unique -> both 'backend' repos keep their own org.
    assert m[bundle_stem_from_url(urls[0])] == "softlex/prima-finance"
    assert m[bundle_stem_from_url(urls[1])] == "softlex/ultima-finance"
    assert m[bundle_stem_from_url(urls[2])] == "softlex/atom"


def test_build_name_map_recovers_leaf(tmp_path):
    from repo_metadata_cli.partner import build_name_map, bundle_stem_from_url
    f = tmp_path / "repos.txt"
    urls = [
        "https://git.softlex.pro/softlex/prima-finance/backend.git",
        "https://git.softlex.pro/softlex/atom/atom-backend.git",
    ]
    f.write_text("\n".join(urls) + "\n")
    m = build_name_map(f)
    assert m[bundle_stem_from_url(urls[0])] == "backend"
    assert m[bundle_stem_from_url(urls[1])] == "atom-backend"


def test_repo_name_property_from_map():
    ctx = _ctx(Path("/tmp/bundles/softlex-prima-finance-backend.bundle"), {})
    ctx.settings.name_map = {"softlex-prima-finance-backend": "backend"}
    assert ctx.repo_name == "backend"
    # No map -> falls back to full bundle stem.
    ctx2 = _ctx(Path("/tmp/bundles/softlex-atom-foo.bundle"), {})
    assert ctx2.repo_name == "softlex-atom-foo"


def test_repo_org_property_from_map():
    ctx = _ctx(Path("/tmp/bundles/cool-repo.bundle"), {})
    ctx.settings.org_map = {"cool-repo": "acme/group"}
    assert ctx.repo_org == "acme/group"
    # missing -> empty string
    ctx2 = _ctx(Path("/tmp/bundles/other.bundle"), {})
    assert ctx2.repo_org == ""
