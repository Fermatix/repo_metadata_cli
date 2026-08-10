"""Regression tests for PR/MR enrichment (codex findings #2 and #3).

All network calls are monkeypatched — no real GitHub/GitLab access.
"""

from __future__ import annotations

import json

from repo_metadata_cli import pr_enricher


# ===========================================================================
# URL → project path: ssh :PORT must not leak into the namespace
# ===========================================================================

def test_gitlab_project_path_ignores_ssh_port():
    hosts = ("gitlab.com", "git.example.com")
    assert pr_enricher._parse_gitlab_project_path(
        "ssh://git@git.example.com:10022/group/sub/repo.git", hosts
    ) == "group%2Fsub%2Frepo"


def test_gitlab_project_path_scp_and_port_variants():
    hosts = ("gitlab.com", "git.internal.example:8443")  # netloc of a base URL may carry a port
    assert pr_enricher._parse_gitlab_project_path(
        "git@git.internal.example:group/repo.git", hosts) == "group%2Frepo"
    assert pr_enricher._parse_gitlab_project_path(
        "https://git.internal.example:8443/group/sub/repo.git", hosts) == "group%2Fsub%2Frepo"
    assert pr_enricher._parse_gitlab_project_path(
        "https://gitlab.com/group/repo.git", hosts) == "group%2Frepo"
    assert pr_enricher._parse_gitlab_project_path(
        "https://github.com/org/repo.git", hosts) is None


# ===========================================================================
# Finding #2: PR counts no longer silently capped at 1000
# ===========================================================================

def test_gitlab_total_uses_x_total_header(monkeypatch):
    # Exact counts come from the X-Total headers, independent of pagination depth.
    monkeypatch.setattr(pr_enricher, "_gitlab_total_count", lambda *a, **k: 1200)
    monkeypatch.setattr(pr_enricher, "_http_get", lambda *a, **k: [])  # no pages to walk

    res = pr_enricher.fetch_gitlab_repo("stem", "grp/proj", "tok")
    assert res is not None
    assert res["total_pr"] == 1200
    assert res["merged_pr"] == 1200


def test_gitlab_all_states_total_differs_from_merged(monkeypatch):
    # total_pr = state=all, merged_pr = state=merged — two different headers.
    def fake_total(base, enc, headers, state="merged"):
        return {"merged": 1200, "all": 1303}[state]

    monkeypatch.setattr(pr_enricher, "_gitlab_total_count", fake_total)
    monkeypatch.setattr(pr_enricher, "_http_get", lambda *a, **k: [])

    res = pr_enricher.fetch_gitlab_repo("stem", "grp/proj", "tok")
    assert res["total_pr"] == 1303
    assert res["merged_pr"] == 1200


def test_gitlab_missing_all_header_degrades_to_merged(monkeypatch):
    # Without the state=all header the true total is unknown -> equals merged.
    def fake_total(base, enc, headers, state="merged"):
        return {"merged": 42, "all": None}[state]

    monkeypatch.setattr(pr_enricher, "_gitlab_total_count", fake_total)
    monkeypatch.setattr(pr_enricher, "_http_get", lambda *a, **k: [])

    res = pr_enricher.fetch_gitlab_repo("stem", "grp/proj", "tok")
    assert res["total_pr"] == 42
    assert res["merged_pr"] == 42


def test_gitlab_pagination_truncation_warns(monkeypatch, caplog):
    # No X-Total header → fall back to pagination; a never-ending list must warn
    # (not silently truncate) and still count every scanned page.
    monkeypatch.setattr(pr_enricher, "_gitlab_total_count", lambda *a, **k: None)
    full_page = [{"reviewers": [], "user_notes_count": 0} for _ in range(pr_enricher._PR_PAGE_SIZE)]
    monkeypatch.setattr(pr_enricher, "_http_get", lambda *a, **k: full_page)

    with caplog.at_level("WARNING"):
        res = pr_enricher.fetch_gitlab_repo("stem", "grp/proj", "tok")

    assert res["total_pr"] == pr_enricher._PR_PAGE_SIZE * pr_enricher._MAX_PR_PAGES
    assert res["merged_pr"] == res["total_pr"]
    assert any("cap" in r.message.lower() for r in caplog.records)


def test_github_reviewed_pagination_truncation_warns(monkeypatch, caplog):
    # A repo with an endless stream of reviewed PRs must warn when the page cap hits.
    endless = {
        "data": {"repository": {"pullRequests": {
            "pageInfo": {"hasNextPage": True, "endCursor": "c"},
            "nodes": [{"reviews": {"totalCount": 1}}],
        }}}
    }
    monkeypatch.setattr(pr_enricher, "_http_post", lambda *a, **k: endless)

    with caplog.at_level("WARNING"):
        pr_enricher._fetch_additional_pages_github("owner", "repo", "cursor0", {})

    assert any("cap" in r.message.lower() for r in caplog.records)


# ===========================================================================
# Finding #3: zero-count cache entries are re-enriched (not skipped forever)
# ===========================================================================

def test_zero_gitlab_cache_entry_is_retried(tmp_path, monkeypatch):
    url = "https://gitlab.com/grp/proj"
    stem = pr_enricher._repo_only_name(url)

    repos = tmp_path / "repos.txt"
    repos.write_text(url + "\n", encoding="utf-8")

    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({stem: {"total_pr": 0, "reviewed_pr": 0, "url": url}}), encoding="utf-8")

    calls = []

    def fake_fetch(bundle_stem, project_path, token, base_url=pr_enricher._GITLAB_REST_BASE):
        calls.append(bundle_stem)
        return {"total_pr": 5, "reviewed_pr": 2, "url": url}

    monkeypatch.setattr(pr_enricher, "fetch_gitlab_repo", fake_fetch)

    pr_enricher.enrich_pr_cache(repos, cache, gitlab_token="tok")

    assert calls == [stem]  # the zero entry was retried
    data = json.loads(cache.read_text(encoding="utf-8"))
    assert data[stem]["total_pr"] == 5
    assert data[stem]["reviewed_pr"] == 2


def test_nonzero_gitlab_cache_entry_is_skipped(tmp_path, monkeypatch):
    url = "https://gitlab.com/grp/proj"
    stem = pr_enricher._repo_only_name(url)

    repos = tmp_path / "repos.txt"
    repos.write_text(url + "\n", encoding="utf-8")

    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({stem: {"total_pr": 9, "reviewed_pr": 3, "url": url}}), encoding="utf-8")

    calls = []
    monkeypatch.setattr(pr_enricher, "fetch_gitlab_repo",
                        lambda *a, **k: calls.append(1) or {"total_pr": 0, "reviewed_pr": 0, "url": url})

    pr_enricher.enrich_pr_cache(repos, cache, gitlab_token="tok")

    assert calls == []  # already-good entry is not re-fetched
    data = json.loads(cache.read_text(encoding="utf-8"))
    assert data[stem]["total_pr"] == 9
