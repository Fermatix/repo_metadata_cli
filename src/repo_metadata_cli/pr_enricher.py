"""Batch-fetch reviewed PR counts from GitHub / GitLab and write a JSON cache.

Cache format (pr_cache.json):
    {
        "<bundle_stem>": {"total_pr": 150, "reviewed_pr": 142, "url": "https://..."},
        ...
    }

Bundle stem = filename without .bundle, matching ctx.bundle_name in the pipeline.
For repos named identically across different orgs, rename bundles to avoid collisions.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
_GITLAB_REST_BASE = "https://gitlab.com/api/v4"

# Repos per GraphQL alias batch.  Keep low enough to stay under GitHub's
# 500 000 node-hour budget; 20 × 100 PRs × 1 review-check = 2000 nodes/query.
_GITHUB_BATCH_SIZE = 20
# Merged PRs fetched per repo per page.  Higher = fewer round-trips but
# more expensive queries.
_PR_PAGE_SIZE = 100
# Maximum pages of PRs per repo (capped to avoid unbounded runtime).
_MAX_PR_PAGES = 10

_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 5


def _repo_only_name(url: str) -> str:
    """Return the last path segment of a repo URL, sanitised (mirrors fetch script)."""
    url = url.strip().rstrip("/").removesuffix(".git")
    segment = url.split("/")[-1]
    segment = re.sub(r"[^A-Za-z0-9.\-]", "-", segment)
    segment = re.sub(r"-+", "-", segment).strip("-.")
    return segment or "repo"


def _parse_github_owner_repo(url: str) -> Optional[Tuple[str, str]]:
    """Return (owner, repo) for a github.com URL, or None."""
    url = url.strip().removesuffix(".git")
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+)$", url)
    if not m:
        return None
    return m.group(1), m.group(2)


def _parse_gitlab_project_path(url: str) -> Optional[str]:
    """Return URL-encoded project path for a gitlab.com URL, or None."""
    url = url.strip().removesuffix(".git")
    m = re.search(r"gitlab\.com[:/](.+)$", url)
    if not m:
        return None
    return m.group(1).replace("/", "%2F")


def _find_bundle(bundles_dir: Path, stem: str) -> Optional[Path]:
    """Return the first *.bundle file whose stem matches, searching recursively."""
    for bundle in bundles_dir.rglob(f"{stem}.bundle"):
        return bundle
    return None


def _extract_original_gitlab_path(bundle_path: Path) -> Optional[str]:
    """Clone bundle into a temp bare repo and grep merge-commit bodies for
    'See merge request PROJECT_PATH!NNN' — the canonical GitLab fingerprint.

    Returns the unencoded project path (e.g. 'org/group/repo'), or None.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        repo_dir = Path(tmpdir) / "repo"
        r = subprocess.run(
            ["git", "clone", "--bare", str(bundle_path), str(repo_dir)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if r.returncode != 0:
            return None
        try:
            out = subprocess.check_output(
                ["git", "-C", str(repo_dir), "log", "--all", "--merges", "--format=%b"],
                stderr=subprocess.DEVNULL,
                timeout=60,
            ).decode("utf-8", errors="ignore")
        except Exception:
            return None

    m = re.search(r"See merge request ([^!\s\n]+)!", out)
    if m:
        return m.group(1).strip()
    return None


def _http_post(url: str, payload: dict, headers: dict) -> dict:
    """POST JSON payload; retry on transient errors with exponential back-off."""
    import requests  # transitive dep — always present

    delay = 2.0
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.post(url, json=payload, headers=headers, timeout=30)
        except Exception as exc:
            if attempt == _MAX_RETRIES:
                raise
            logger.warning("Request error (attempt %d/%d): %s — retrying in %.0fs",
                           attempt, _MAX_RETRIES, exc, delay)
            time.sleep(delay)
            delay *= 2
            continue

        if resp.status_code in _RETRY_STATUSES:
            retry_after = int(resp.headers.get("Retry-After", delay))
            logger.warning("HTTP %d (attempt %d/%d) — retrying in %ds",
                           resp.status_code, attempt, _MAX_RETRIES, retry_after)
            time.sleep(retry_after)
            delay = max(delay * 2, retry_after)
            continue

        resp.raise_for_status()
        return resp.json()

    raise RuntimeError(f"All {_MAX_RETRIES} retries exhausted for {url}")


def _http_get(url: str, headers: dict, params: Optional[dict] = None) -> dict | list:
    import requests

    delay = 2.0
    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)
        except Exception as exc:
            if attempt == _MAX_RETRIES:
                raise
            logger.warning("GET error (attempt %d/%d): %s — retrying in %.0fs",
                           attempt, _MAX_RETRIES, exc, delay)
            time.sleep(delay)
            delay *= 2
            continue

        if resp.status_code in _RETRY_STATUSES:
            retry_after = int(resp.headers.get("Retry-After", delay))
            logger.warning("HTTP %d — retrying in %ds", resp.status_code, retry_after)
            time.sleep(retry_after)
            delay = max(delay * 2, retry_after)
            continue

        resp.raise_for_status()
        return resp.json()

    raise RuntimeError(f"All {_MAX_RETRIES} retries exhausted for {url}")


# ---------------------------------------------------------------------------
# GitHub GraphQL batching
# ---------------------------------------------------------------------------

def _build_github_batch_query(batch: list[Tuple[str, str, str]]) -> str:
    """Build a GraphQL query with one alias per (alias, owner, repo) tuple.

    Returns total merged PRs and how many of the first _PR_PAGE_SIZE had reviews.
    """
    fragments = []
    for alias, owner, repo in batch:
        fragments.append(f"""
  {alias}: repository(owner: {json.dumps(owner)}, name: {json.dumps(repo)}) {{
    totalPRs: pullRequests(states: MERGED, first: 1) {{
      totalCount
    }}
    firstPage: pullRequests(states: MERGED, first: {_PR_PAGE_SIZE}, orderBy: {{field: CREATED_AT, direction: DESC}}) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{
        reviews(first: 1) {{ totalCount }}
      }}
    }}
  }}""")
    return "{\n" + "\n".join(fragments) + "\n}"


def _count_reviewed_in_nodes(nodes: list) -> int:
    return sum(1 for n in nodes if n.get("reviews", {}).get("totalCount", 0) > 0)


def _fetch_additional_pages_github(
    owner: str,
    repo: str,
    cursor: str,
    headers: dict,
) -> int:
    """Paginate through remaining PR pages and return reviewed-PR count."""
    reviewed = 0
    for _ in range(_MAX_PR_PAGES - 1):
        query = f"""{{
  repository(owner: {json.dumps(owner)}, name: {json.dumps(repo)}) {{
    pullRequests(states: MERGED, first: {_PR_PAGE_SIZE}, after: {json.dumps(cursor)}, orderBy: {{field: CREATED_AT, direction: DESC}}) {{
      pageInfo {{ hasNextPage endCursor }}
      nodes {{
        reviews(first: 1) {{ totalCount }}
      }}
    }}
  }}
}}"""
        data = _http_post(_GITHUB_GRAPHQL_URL, {"query": query}, headers)
        prs = data.get("data", {}).get("repository", {}).get("pullRequests", {})
        reviewed += _count_reviewed_in_nodes(prs.get("nodes", []))
        page_info = prs.get("pageInfo", {})
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info["endCursor"]
    return reviewed


def fetch_github_batch(
    repos: list[Tuple[str, str, str]],  # (bundle_stem, owner, repo_name)
    token: str,
    existing_cache: Dict[str, dict],
) -> Dict[str, dict]:
    """Fetch PR stats for up to _GITHUB_BATCH_SIZE repos in one GraphQL request."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    # Skip repos already in cache
    to_fetch = [(stem, owner, name) for stem, owner, name in repos
                if stem not in existing_cache]
    if not to_fetch:
        return {}

    # Batch into groups
    results: Dict[str, dict] = {}
    for i in range(0, len(to_fetch), _GITHUB_BATCH_SIZE):
        batch = to_fetch[i: i + _GITHUB_BATCH_SIZE]
        aliases = [(f"r{j}", owner, name) for j, (_, owner, name) in enumerate(batch)]
        query = _build_github_batch_query(aliases)

        try:
            data = _http_post(_GITHUB_GRAPHQL_URL, {"query": query}, headers)
        except Exception as exc:
            logger.error("GitHub GraphQL batch failed: %s", exc)
            continue

        if "errors" in data:
            logger.warning("GraphQL errors: %s", data["errors"])

        gql_data = data.get("data") or {}
        for j, (stem, owner, name) in enumerate(batch):
            alias = f"r{j}"
            repo_data = gql_data.get(alias)
            if not repo_data:
                logger.warning("No data for %s/%s", owner, name)
                continue

            total_pr = repo_data.get("totalPRs", {}).get("totalCount", 0)
            first_page = repo_data.get("firstPage", {})
            nodes = first_page.get("nodes", [])
            reviewed = _count_reviewed_in_nodes(nodes)

            # Paginate if there are more PRs
            page_info = first_page.get("pageInfo", {})
            if page_info.get("hasNextPage") and page_info.get("endCursor"):
                try:
                    reviewed += _fetch_additional_pages_github(
                        owner, name, page_info["endCursor"], headers
                    )
                except Exception as exc:
                    logger.warning("Pagination failed for %s/%s: %s", owner, name, exc)

            results[stem] = {
                "total_pr": total_pr,
                "reviewed_pr": reviewed,
                "url": f"https://github.com/{owner}/{name}",
            }

    return results


# ---------------------------------------------------------------------------
# GitLab REST API
# ---------------------------------------------------------------------------

def fetch_gitlab_repo(
    bundle_stem: str,
    project_path: str,
    token: str,
    base_url: str = _GITLAB_REST_BASE,
) -> Optional[dict]:
    """Fetch total and reviewed MR count for one GitLab project.

    project_path may contain '/' or '%2F' separators — both are handled.
    total_pr is accumulated across all paginated pages (not from X-Total header
    which is unavailable without access to raw HTTP headers).
    """
    headers = {"PRIVATE-TOKEN": token, "Content-Type": "application/json"}
    # Normalise: decode any existing %2F then re-encode so the path is uniform.
    decoded_path = project_path.replace("%2F", "/")
    encoded = decoded_path.replace("/", "%2F")

    logger.info("GitLab fetching MRs for %s (encoded: %s)", decoded_path, encoded)

    total_mr = 0
    reviewed = 0
    page = 1

    for _ in range(_MAX_PR_PAGES):
        try:
            mrs = _http_get(
                f"{base_url}/projects/{encoded}/merge_requests",
                headers,
                params={
                    "state": "merged",
                    "per_page": _PR_PAGE_SIZE,
                    "page": page,
                    "with_merge_status_recheck": "false",
                },
            )
        except Exception as exc:
            logger.warning("GitLab MR list failed for %s page %d: %s",
                           decoded_path, page, exc)
            if page == 1:
                return None
            break

        if not isinstance(mrs, list) or not mrs:
            break

        total_mr += len(mrs)

        for mr in mrs:
            # GitLab 13.8+ (EE/gitlab.com) includes `reviewers` in the MR list
            # response — no extra API call needed.  Fall back to user_notes_count
            # as a proxy for review activity on older/CE instances.
            reviewers = mr.get("reviewers") or []
            if reviewers or mr.get("user_notes_count", 0) > 0:
                reviewed += 1

        if len(mrs) < _PR_PAGE_SIZE:
            break
        page += 1

    logger.info("GitLab %s: total_mr=%d reviewed_pr=%d", decoded_path, total_mr, reviewed)
    return {
        "total_pr": total_mr,
        "reviewed_pr": reviewed,
        "url": f"https://gitlab.com/{encoded}",
    }


# ---------------------------------------------------------------------------
# Main enrichment entry point
# ---------------------------------------------------------------------------

def enrich_pr_cache(
    repos_file: Path,
    cache_file: Path,
    bundles_dir: Optional[Path] = None,
    github_token: Optional[str] = None,
    gitlab_token: Optional[str] = None,
    gitlab_base_url: str = _GITLAB_REST_BASE,
) -> None:
    """Read repos_file, query APIs in batches, write/update cache_file.

    When bundles_dir is provided, GitLab mirror repos (which have 0 MRs via the
    mirror URL) are automatically resolved to their original project path by
    scanning the bundle's merge-commit bodies for the canonical GitLab fingerprint
    'See merge request ORIGINAL_PATH!NNN'.  Cache entries with total_pr == 0 are
    also retried so a re-run with --bundles-dir corrects previously failed lookups.
    """
    # Load existing cache for resume support
    existing: Dict[str, dict] = {}
    if cache_file.exists():
        try:
            existing = json.loads(cache_file.read_text(encoding="utf-8"))
            logger.info("Loaded %d cached entries from %s", len(existing), cache_file)
        except Exception as exc:
            logger.warning("Could not load existing cache: %s — starting fresh", exc)

    urls = [
        line.strip()
        for line in repos_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.startswith("#")
    ]
    logger.info("Processing %d repo URLs from %s", len(urls), repos_file)

    github_batch: list[Tuple[str, str, str]] = []  # (stem, owner, repo)
    gitlab_list: list[Tuple[str, str]] = []         # (stem, project_path_encoded)
    unrecognised: list[str] = []

    for url in urls:
        stem = _repo_only_name(url)
        gh = _parse_github_owner_repo(url)
        if gh:
            github_batch.append((stem, gh[0], gh[1]))
            continue
        gl = _parse_gitlab_project_path(url)
        if gl:
            resolved_path = gl
            # If the cached entry for this stem is zero (or absent) and we have
            # bundles available, try to find the *original* project path from
            # the bundle's merge-commit history.
            cached_entry = existing.get(stem)
            needs_resolve = bundles_dir and (
                cached_entry is None or cached_entry.get("total_pr", 0) == 0
            )
            if needs_resolve:
                bundle_path = _find_bundle(bundles_dir, stem)
                if bundle_path:
                    original = _extract_original_gitlab_path(bundle_path)
                    if original:
                        logger.info("Resolved original GitLab path for %s: %s", stem, original)
                        resolved_path = original.replace("/", "%2F")
            gitlab_list.append((stem, resolved_path))
            continue
        unrecognised.append(url)

    if unrecognised:
        logger.warning("%d URLs not recognised as GitHub or GitLab: %s",
                       len(unrecognised), unrecognised[:5])

    cache = dict(existing)

    # ---- GitHub ----
    if github_batch and github_token:
        skip = sum(1 for s, _, _ in github_batch if s in cache)
        todo = len(github_batch) - skip
        logger.info("GitHub: %d repos (%d cached, %d to fetch)", len(github_batch), skip, todo)
        new_results = fetch_github_batch(github_batch, github_token, cache)
        cache.update(new_results)
        logger.info("GitHub: fetched %d entries", len(new_results))
    elif github_batch and not github_token:
        logger.warning("GitHub repos found but GITHUB_TOKEN not provided — skipping")

    # ---- GitLab ----
    if gitlab_list and gitlab_token:
        # Count entries that need fetching: absent or previously returned total_pr=0
        todo = sum(
            1 for s, _ in gitlab_list
            if cache.get(s, {}).get("total_pr", 0) == 0
        )
        skip = len(gitlab_list) - todo
        logger.info("GitLab: %d repos (%d cached, %d to fetch)", len(gitlab_list), skip, todo)
        for stem, path in gitlab_list:
            if cache.get(stem, {}).get("total_pr", 0) > 0:
                continue  # already have a valid non-zero count
            result = fetch_gitlab_repo(stem, path, gitlab_token, gitlab_base_url)
            if result:
                cache[stem] = result
            # Save after every repo for resume safety
            _save_cache(cache_file, cache)
    elif gitlab_list and not gitlab_token:
        logger.warning("GitLab repos found but GITLAB_TOKEN not provided — skipping")

    _save_cache(cache_file, cache)
    logger.info("PR cache saved to %s (%d entries total)", cache_file, len(cache))


def _save_cache(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
