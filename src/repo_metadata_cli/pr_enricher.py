"""Batch-fetch PR counts from GitHub / GitLab and write a JSON cache.

Cache format (pr_cache.json):
    {
        "<bundle_stem>": {"total_pr": 163, "merged_pr": 150, "reviewed_pr": 142,
                          "url": "https://..."},
        ...
    }

``total_pr`` counts ALL states (merged + open + closed), ``merged_pr`` only
merged ones, ``reviewed_pr`` merged ones with review activity.  Entries
written by older versions lack ``merged_pr`` (their ``total_pr`` was the
merged count) — the pipeline treats those as merged-only and falls back to
git fingerprints for the merged column.

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
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
_GITLAB_DOMAIN = "gitlab.com"
_GITLAB_REST_BASE = f"https://{_GITLAB_DOMAIN}/api/v4"

# Repos per GraphQL alias batch.  Keep low enough to stay under GitHub's
# 500 000 node-hour budget; 20 × 100 PRs × 1 review-check = 2000 nodes/query.
_GITHUB_BATCH_SIZE = 20
# Merged PRs fetched per repo per page.  Higher = fewer round-trips but
# more expensive queries.
_PR_PAGE_SIZE = 100
# Maximum pages of PRs per repo (safety bound against unbounded runtime).
# 100 pages × 100 = 10 000 items.  When the cap is hit we LOG a warning so the
# truncation is never silent (GitLab total_pr is taken from the X-Total header,
# so it stays exact even beyond this cap).
_MAX_PR_PAGES = 100

_RETRY_STATUSES = {429, 500, 502, 503, 504}
_MAX_RETRIES = 5


def _repo_only_name(url: str) -> str:
    """Bundle stem used as the pr_cache key.

    Must equal ``ctx.bundle_name`` — the on-disk ``*.bundle`` stem — or P/Q cache
    lookups silently miss. Since bundles are named by the FULL namespace path
    (`partner.bundle_stem_from_url`, to avoid leaf collisions across groups), the
    cache key is the same full-path stem. Delegate to the single source of truth
    so the two can never drift again.
    """
    from .partner import bundle_stem_from_url  # local import: keep modules decoupled
    return bundle_stem_from_url(url)


def _parse_github_owner_repo(url: str) -> Optional[Tuple[str, str]]:
    """Return (owner, repo) for a github.com URL, or None."""
    url = url.strip().removesuffix(".git")
    m = re.search(r"github\.com[:/]([^/]+)/([^/]+)$", url)
    if not m:
        return None
    return m.group(1), m.group(2)


def _parse_gitlab_project_path(
    url: str, gitlab_hosts: tuple[str, ...] = (_GITLAB_DOMAIN,)
) -> Optional[str]:
    """Return URL-encoded project path for a GitLab URL, or None.

    Checks all hosts in gitlab_hosts so self-hosted instances are supported.
    Hosts are compared by hostname only, and for scheme URLs the path comes
    from urlparse — the :PORT of ssh://host:PORT/... stays in netloc and never
    leaks into the project path (it used to produce 404s like
    projects/10022%2Fgroup%2Frepo on self-hosted instances).
    """
    url = url.strip().removesuffix(".git")
    hostnames = {h.rsplit("@", 1)[-1].split(":", 1)[0] for h in gitlab_hosts}
    if "://" in url:
        parsed = urlparse(url)
        if parsed.hostname in hostnames:
            path = parsed.path.strip("/")
            return path.replace("/", "%2F") if path else None
        return None
    for host in hostnames:
        m = re.search(rf"{re.escape(host)}[:/](.+)$", url)
        if m:
            return m.group(1).replace("/", "%2F")
    return None


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
                timeout=360,
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
            resp = requests.post(url, json=payload, headers=headers, timeout=360)
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

    Returns the all-states PR total, the merged-PR total, and how many of the
    first _PR_PAGE_SIZE merged PRs had reviews.
    """
    fragments = []
    for alias, owner, repo in batch:
        fragments.append(f"""
  {alias}: repository(owner: {json.dumps(owner)}, name: {json.dumps(repo)}) {{
    allPRs: pullRequests(first: 1) {{
      totalCount
    }}
    mergedPRs: pullRequests(states: MERGED, first: 1) {{
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
    else:
        # Loop exhausted the page cap without seeing the last page.
        logger.warning(
            "GitHub %s/%s: hit the %d-page cap; reviewed_pr may be undercounted.",
            owner, repo, _MAX_PR_PAGES,
        )
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
    # Skip repos already cached with a non-zero count; retry zero/absent ones.
    def _cached_ok(stem: str) -> bool:
        entry = existing_cache.get(stem)
        return entry is not None and entry.get("total_pr", 0) > 0

    to_fetch = [(stem, owner, name) for stem, owner, name in repos
                if not _cached_ok(stem)]
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

            merged_pr = repo_data.get("mergedPRs", {}).get("totalCount", 0)
            # All-states total; a repo where the alias is missing (partial
            # GraphQL errors) degrades to the merged count — never below it.
            total_pr = max(
                repo_data.get("allPRs", {}).get("totalCount", 0), merged_pr
            )
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
                "merged_pr": merged_pr,
                "reviewed_pr": reviewed,
                "url": f"https://github.com/{owner}/{name}",
            }

    return results


# ---------------------------------------------------------------------------
# GitLab REST API
# ---------------------------------------------------------------------------

def _gitlab_total_count(
    base_url: str, encoded: str, headers: dict, state: str = "merged"
) -> Optional[int]:
    """Exact MR count for one ``state`` from GitLab's ``X-Total`` header
    (one cheap request).  ``state="all"`` counts every MR regardless of state.

    Returns None when the header is absent (GitLab omits it for very large or
    keyset-paginated collections) so the caller can fall back to pagination.
    """
    import requests

    try:
        resp = requests.get(
            f"{base_url}/projects/{encoded}/merge_requests",
            headers=headers,
            params={"state": state, "per_page": 1, "page": 1,
                    "with_merge_status_recheck": "false"},
            timeout=30,
        )
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001 — best-effort; pagination is the fallback
        logger.debug("GitLab X-Total lookup failed for %s (state=%s): %s",
                     encoded, state, exc)
        return None
    total = resp.headers.get("X-Total")
    try:
        return int(total) if total is not None else None
    except ValueError:
        return None


def fetch_gitlab_repo(
    bundle_stem: str,
    project_path: str,
    token: str,
    base_url: str = _GITLAB_REST_BASE,
) -> Optional[dict]:
    """Fetch the all-states total, merged and reviewed MR counts for one
    GitLab project.

    project_path may contain '/' or '%2F' separators — both are handled.
    total_pr (state=all) and merged_pr (state=merged) come from the exact
    ``X-Total`` headers when available (so they are not capped by pagination);
    reviewed_pr is accumulated by paging the merged list up to _MAX_PR_PAGES,
    with a warning when the cap is reached.
    """
    headers = {"PRIVATE-TOKEN": token, "Content-Type": "application/json"}
    # Normalise: decode any existing %2F then re-encode so the path is uniform.
    decoded_path = project_path.replace("%2F", "/")
    encoded = decoded_path.replace("/", "%2F")

    logger.info("GitLab fetching MRs for %s (encoded: %s)", decoded_path, encoded)

    exact_merged = _gitlab_total_count(base_url, encoded, headers, state="merged")
    exact_all = _gitlab_total_count(base_url, encoded, headers, state="all")

    paged_total = 0
    reviewed = 0
    page = 1
    truncated = True  # set False when we exhaust the list before the page cap

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
            if page == 1 and exact_merged is None:
                return None
            truncated = False
            break

        if not isinstance(mrs, list) or not mrs:
            truncated = False
            break

        paged_total += len(mrs)

        for mr in mrs:
            # GitLab 13.8+ (EE/gitlab.com) includes `reviewers` in the MR list
            # response — no extra API call needed.  Fall back to user_notes_count
            # as a proxy for review activity on older/CE instances.
            reviewers = mr.get("reviewers") or []
            if reviewers or mr.get("user_notes_count", 0) > 0:
                reviewed += 1

        if len(mrs) < _PR_PAGE_SIZE:
            truncated = False
            break
        page += 1

    if truncated:
        logger.warning(
            "GitLab %s: hit the %d-page cap (%d MRs scanned); reviewed_pr may be undercounted.",
            decoded_path, _MAX_PR_PAGES, paged_total,
        )

    merged_mr = exact_merged if exact_merged is not None else paged_total
    # Without the all-states header the true total is unknown — degrade to the
    # merged count (never below it), same honest fallback as fingerprints.
    total_mr = max(exact_all, merged_mr) if exact_all is not None else merged_mr
    logger.info("GitLab %s: total_mr=%d merged_mr=%d reviewed_pr=%d",
                decoded_path, total_mr, merged_mr, reviewed)
    parsed_base = urlparse(base_url)
    instance_url = f"{parsed_base.scheme}://{parsed_base.netloc}"
    return {
        "total_pr": total_mr,
        "merged_pr": merged_mr,
        "reviewed_pr": reviewed,
        "url": f"{instance_url}/{decoded_path}",
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

    # Build the set of GitLab hosts to recognise: always gitlab.com + the custom instance host.
    _custom_host = urlparse(gitlab_base_url).netloc
    gitlab_hosts = tuple({_GITLAB_DOMAIN, _custom_host} - {""})

    github_batch: list[Tuple[str, str, str]] = []  # (stem, owner, repo)
    gitlab_list: list[Tuple[str, str]] = []         # (stem, project_path_encoded)
    unrecognised: list[str] = []

    for url in urls:
        stem = _repo_only_name(url)
        gh = _parse_github_owner_repo(url)
        if gh:
            github_batch.append((stem, gh[0], gh[1]))
            continue
        gl = _parse_gitlab_project_path(url, gitlab_hosts=gitlab_hosts)
        if gl:
            resolved_path = gl
            # If the cached entry for this stem is zero (or absent) and we have
            # bundles available, try to find the *original* project path from
            # the bundle's merge-commit history.
            cached_entry = existing.get(stem)
            # Resolve the original path for absent OR zero-count entries, so a
            # re-run with --bundles-dir corrects previously failed/empty lookups.
            needs_resolve = bool(bundles_dir) and (
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
    def _gl_cached_ok(stem: str) -> bool:
        entry = cache.get(stem)
        return entry is not None and entry.get("total_pr", 0) > 0

    if gitlab_list and gitlab_token:
        todo = sum(1 for s, _ in gitlab_list if not _gl_cached_ok(s))
        skip = len(gitlab_list) - todo
        logger.info("GitLab: %d repos (%d cached, %d to fetch)", len(gitlab_list), skip, todo)
        for stem, path in gitlab_list:
            if _gl_cached_ok(stem):
                continue  # only skip non-zero cached entries; retry zeros
            result = fetch_gitlab_repo(stem, path, gitlab_token, gitlab_base_url)
            # Always cache, even on 404/failure — prevents infinite retries
            cache[stem] = result or {
                "total_pr": 0, "merged_pr": 0, "reviewed_pr": 0, "url": path,
            }
            _save_cache(cache_file, cache)
    elif gitlab_list and not gitlab_token:
        logger.warning("GitLab repos found but GITLAB_TOKEN not provided — skipping")

    _save_cache(cache_file, cache)
    logger.info("PR cache saved to %s (%d entries total)", cache_file, len(cache))


def _save_cache(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
