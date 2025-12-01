"""High-level repository analyzers for metadata and token statistics."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set

import pandas as pd
from tqdm import tqdm

from .allowed_files import AllowedFiles
from .config import DEFAULT_TOKENIZER_ID, TreeSitterConfig
from .metrics import (
    compute_avg_func_length,
    compute_duplication_ratio,
    compute_readme_stats,
    detect_license,
    get_cloc_stats,
    get_contributors_count,
    iter_code_files,
)
from .token_stats import TokenizerProvider
from .tree_sitter_support import TreeSitterManager
from .utils import run_cmd


logger = logging.getLogger(__name__)


def _parse_du_kb(output: str) -> int:
    try:
        return int(output.split()[0])
    except (IndexError, ValueError):
        return 0


def extract_added_lines(diff_text: str, allowed_files: AllowedFiles) -> List[str]:
    """Collect added lines from a git diff, filtering by allowed code paths."""
    added: List[str] = []
    current_file: Optional[str] = None
    current_is_binary = False

    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            current_file = None
            current_is_binary = False
            continue

        if line.startswith("Binary files ") and " differ" in line:
            current_is_binary = True
            continue

        if line.startswith("+++ "):
            path = line[4:].strip()
            if path == "/dev/null":
                current_file = None
                continue
            if path.startswith("b/"):
                path = path[2:]
            current_file = path if allowed_files.is_code_path(path) else None
            continue

        if line.startswith("+") and not line.startswith("+++"):
            if current_file is not None and not current_is_binary:
                added.append(line[1:])

    return added


@dataclass
class RepoAnalyzer:
    allowed_files: AllowedFiles = field(default_factory=AllowedFiles)
    tree_sitter: Optional[TreeSitterManager] = field(
        default_factory=lambda: TreeSitterManager(TreeSitterConfig())
    )
    tokenizer_provider: Optional[TokenizerProvider] = field(
        default_factory=lambda: TokenizerProvider(DEFAULT_TOKENIZER_ID)
    )

    def _clone_bundle(self, bundle_path: Path, dest_dir: Path) -> Optional[Path]:
        repo_dir = dest_dir / bundle_path.stem
        clone_env = os.environ.copy()
        clone_env.setdefault("GIT_LFS_SKIP_SMUDGE", "1")

        result = subprocess.run(
            ["git", "clone", str(bundle_path), str(repo_dir)],
            env=clone_env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if result.returncode != 0 or not repo_dir.exists():
            logger.warning("Failed to clone %s", bundle_path)
            return None
        logger.debug("Cloned %s into %s", bundle_path.name, repo_dir)
        return repo_dir

    def analyze_repo_metadata(self, bundle_path: Path) -> Dict[str, Any]:
        """
        Compute repository metadata excluding token counts.
        """
        logger.debug("Processing metadata for %s", bundle_path.name)
        data: Dict[str, Any] = {
            "repo_name": bundle_path.stem,
            "languages": "",
            "stack": "",
            "license_type": "UNKNOWN",
            "created_at": "",
            "commit_count": 0,
            "branch_count": 0,
            "contributors_count": 0,
            "repo_git_history_mb": 0.0,
            "repo_bundle_mb": 0.0,
            "repo_worktree_mb": 0.0,
            "files": 0,
            "loc": 0,
            "avg_func_length": 0.0,
            "docstring_ratio": 0.0,
            "duplication_ratio": 0.0,
            "documentation_cnt": 0,
        }

        try:
            data["repo_bundle_mb"] = round(bundle_path.stat().st_size / (1024 * 1024), 3)
        except OSError:
            logger.debug("Unable to stat bundle %s", bundle_path)
            data["repo_bundle_mb"] = 0.0

        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = self._clone_bundle(bundle_path, Path(tmpdir))
            if repo_dir is None:
                logger.error("Skipping %s: failed to materialize repository", bundle_path.name)
                return data

            data["created_at"] = run_cmd(
                ["git", "log", "--reverse", "--format=%ai", "--max-count=1"], cwd=repo_dir
            )
            data["commit_count"] = int(
                run_cmd(["git", "rev-list", "--count", "--all"], cwd=repo_dir) or 0
            )
            branches_raw = run_cmd(["git", "branch", "-a"], cwd=repo_dir)
            data["branch_count"] = len([line for line in branches_raw.splitlines() if line.strip()])
            data["contributors_count"] = get_contributors_count(repo_dir)

            du_git = run_cmd(["du", "-sk", str(repo_dir / ".git")])
            git_kb = _parse_du_kb(du_git)
            data["repo_git_history_mb"] = round(git_kb / 1024, 3)

            du_total = run_cmd(["du", "-sk", str(repo_dir)])
            total_kb = _parse_du_kb(du_total)
            worktree_kb = max(total_kb - git_kb, 0)
            data["repo_worktree_mb"] = round(worktree_kb / 1024, 3)

            data["license_type"] = detect_license(repo_dir)
            data["documentation_cnt"] = compute_readme_stats(repo_dir)

            summary, langs = get_cloc_stats(repo_dir)
            if summary:
                n_files = summary.get("nFiles", 0)
                code = summary.get("code", 0)
                comment = summary.get("comment", 0)

                data["files"] = n_files
                data["loc"] = int(code) + int(comment)

                if code > 0:
                    data["docstring_ratio"] = comment / code
                else:
                    data["docstring_ratio"] = 0.0

                lang_code = {lang: s.get("code", 0) for lang, s in langs.items()}
                total_code = sum(lang_code.values())
                if total_code > 0:
                    distribution = {
                        lang: round(c / total_code, 6)
                        for lang, c in lang_code.items()
                        if c > 0
                    }
                    data["languages"] = json.dumps(distribution, ensure_ascii=False)
                    top_langs = sorted(distribution.items(), key=lambda x: -x[1])[:3]
                    data["stack"] = ", ".join(
                        f"{lang} ({share:.0%})" for lang, share in top_langs
                    )
                else:
                    data["languages"] = json.dumps({}, ensure_ascii=False)
                    data["stack"] = ""

            data["avg_func_length"] = compute_avg_func_length(repo_dir, self.allowed_files, self.tree_sitter)
            data["duplication_ratio"] = compute_duplication_ratio(repo_dir, self.allowed_files)

        return data

    def analyze_repo_tokens(self, bundle_path: Path) -> Dict[str, Any]:
        """
        Compute token counts for all commits and last commit snapshot.
        """
        logger.debug("Processing token stats for %s", bundle_path.name)
        data = {
            "repo_name": bundle_path.stem,
            "deepseek_token_count_all_commits": 0,
            "deepseek_token_count_last_commit": 0,
        }

        if self.tokenizer_provider is None:
            logger.debug("Tokenizer is not configured; skipping token stats for %s", bundle_path.name)
            return data

        with tempfile.TemporaryDirectory() as tmpdir:
            repo_dir = self._clone_bundle(bundle_path, Path(tmpdir))
            if repo_dir is None:
                return data

            commit_hashes = run_cmd(["git", "rev-list", "--all"], cwd=repo_dir).splitlines()
            texts_all_commits: List[str] = []

            for commit in commit_hashes:
                if not commit:
                    continue
                try:
                    diff = subprocess.check_output(
                        ["git", "-C", str(repo_dir), "show", commit, "--unified=0", "--no-color"],
                        stderr=subprocess.DEVNULL,
                    ).decode(errors="ignore")
                except subprocess.CalledProcessError:
                    continue

                added_lines = extract_added_lines(diff, self.allowed_files)
                if added_lines:
                    texts_all_commits.append("\n".join(added_lines))

            data["deepseek_token_count_all_commits"] = self.tokenizer_provider.count_tokens_batch(
                texts_all_commits
            )

            texts_last: List[str] = []
            for file_path in iter_code_files(repo_dir, self.allowed_files):
                try:
                    text = file_path.read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    continue
                if text:
                    texts_last.append(text)

            data["deepseek_token_count_last_commit"] = self.tokenizer_provider.count_tokens_batch(
                texts_last
            )

        return data

    def _processed_repos(self, csv_path: Path) -> Set[str]:
        if not csv_path.exists():
            logger.info("%s will be created from scratch.", csv_path)
            return set()
        try:
            existing_df = pd.read_csv(csv_path)
        except Exception as exc:  # pragma: no cover - defensive logging
            logger.warning("Failed to read %s (%s); recomputing all entries.", csv_path, exc)
            return set()

        if existing_df.empty or "repo_name" not in existing_df.columns:
            logger.info("%s exists but is empty/invalid; recomputing all entries.", csv_path)
            return set()
        processed = set(existing_df["repo_name"].astype(str))
        logger.info("%s already contains %d repositories.", csv_path, len(processed))
        return processed

    def _process_bundles(
        self,
        dataset_dir: Path,
        csv_path: Path,
        desc: str,
        analyze_fn: Callable[[Path], Dict[str, Any]],
    ) -> None:
        bundle_files = sorted(dataset_dir.rglob("*.bundle"))
        logger.info("Found %d bundle files under %s", len(bundle_files), dataset_dir)
        if not bundle_files:
            logger.warning("No *.bundle files found under %s; nothing to process.", dataset_dir)
            return
        processed = self._processed_repos(csv_path)

        for bundle_path in tqdm(bundle_files, desc=desc):
            repo_name = bundle_path.stem
            if repo_name in processed:
                logger.debug("Skipping %s (already processed)", repo_name)
                continue

            row = analyze_fn(bundle_path)
            row_df = pd.DataFrame([row])
            row_df.to_csv(
                csv_path,
                mode="a" if csv_path.exists() else "w",
                header=not csv_path.exists(),
                index=False,
            )
            processed.add(repo_name)

        logger.info("%s pipeline finished; %d repositories processed.", desc, len(processed))

    def run_metadata_pipeline(self, dataset_dir: Path, csv_metadata_path: Path) -> None:
        self._process_bundles(dataset_dir, csv_metadata_path, "Metadata", self.analyze_repo_metadata)

    def run_tokens_pipeline(self, dataset_dir: Path, csv_tokens_path: Path) -> None:
        if self.tokenizer_provider is None:
            logger.warning("Tokenizer provider is not configured; token stats will be zeros.")
        self._process_bundles(dataset_dir, csv_tokens_path, "Tokens", self.analyze_repo_tokens)

    @staticmethod
    def merge_metadata_and_tokens(
        csv_metadata_path: Path,
        csv_tokens_path: Path,
        output_file: Path,
    ):
        import pandas as pd  # type: ignore

        df_meta = pd.read_csv(csv_metadata_path)
        df_tokens = pd.read_csv(csv_tokens_path)

        df = pd.merge(df_meta, df_tokens, on="repo_name", how="left")
        df.to_csv(output_file, index=False)
        logger.info("Merged metadata and tokens into %s", output_file)
        return df
