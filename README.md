# repo-metadata-cli

A command-line utility for extracting repository metadata from Git bundle files. It is designed for dataset curation and code quality assessment: it identifies licenses, characterizes the technology stack, quantifies history and language distribution, measures documentation and duplication, estimates average function length via Tree-sitter, and optionally performs tokenization for all commits and the final snapshot.

## Capabilities
- Processes full datasets of `*.bundle` files in a single pass.
- History metrics: creation date, counts of commits and branches, sizes of `.git` and the working tree.
- Code quality: `cloc` counts (files, code/comment lines), language distribution, duplication ratio, average function length via Tree-sitter, README volume.
- License discovery: fast detection via LICENSE/COPYING files.
- Tokenization: aggregated tokens for all commits and for the latest snapshot (HuggingFace tokenizer).
- Configuration is entirely TOML-based; no hard-coded language lists.

## Requirements
- Python 3.10+
- [uv](https://github.com/astral-sh/uv) as the environment and dependency manager
- Utilities: `git`, `cloc`
- Optional: internet access to download tokenizers and grammars

## Installation via uv
```bash
uv venv                       # create .venv
source .venv/bin/activate     # activate the environment
uv sync                       # install dependencies from pyproject.toml and generate uv.lock
uv run repo-metadata --help   # verify the CLI
```

The commands below assume an active virtual environment or the `uv run` prefix.

## Configuration (`repo_metadata.toml`)
All settings reside in a TOML file (default: `repo_metadata.toml` in the working directory). A reference is provided in `repo_metadata.toml.example`.

- `[files]`
  - `allowed_extensions`: list of extensions treated as code. If omitted, `tree_sitter.extension_language_map` keys are used.
  - `allowed_filenames`: extensionless filenames that are always included (e.g., Makefile, Dockerfile).
- `[tree_sitter]`
  - `language_packages`: Python packages with grammars installable via `fetch-grammars`.
  - `extension_language_map`: mapping from file extension to language (keys normalized to lowercase with a dot).
  - `lang_func_node_types`: node types considered functions when computing average length.
  - `vendor_dir` / `language_repo_map`: optional local grammar paths when building from source.

## Core commands
All commands accept `--config-file` (path to TOML) and `--log-level` for logging control.

### Metadata (no tokens)
```bash
uv run repo-metadata metadata /path/to/dataset \
  --output-csv repo_metadata.csv \
  --config-file repo_metadata.toml
```
- Scans all `*.bundle` files under `dataset_dir`, clones each into a temporary directory, computes metrics, and appends them to the CSV.
- The `--skip-tree-sitter` flag disables average function length computation.

### Tokens
```bash
uv run repo-metadata tokens /path/to/dataset \
  --output-csv repo_tokens.csv \
  --config-file repo_metadata.toml \
  --tokenizer-id deepseek-ai/deepseek-coder-6.7b-base
```
- If `--tokenizer-id` is omitted and `TOKENIZER_ID` is not set, token counts are skipped.
- The tokenizer is resolved via `transformers`; without the package or internet access, tokenization is skipped.

### Merging tables
```bash
uv run repo-metadata merge repo_metadata.csv repo_tokens.csv \
  --output-csv repo_metadata_with_tokens.csv
```

## Output fields

| field_name | type | description | examples_or_rules |
| --- | --- | --- | --- |
| repo_name | string | Repository name (bundle stem); used as the key for `merge`. | openai/gym |
| languages | stringified JSON | Language distribution by share of LoC; JSON of the form `{lang: share}`. | {"Python":0.72,"C++":0.18} |
| stack | string | Human-readable top 3 languages with percentages. | Python (72%), C++ (18%), C (6%) |
| license_type | enum | Detected root license: MIT, APACHE-2.0, GPL, GPL-3.0, BSD, MPL-2.0, UNLICENSE, UNKNOWN. | MIT |
| created_at | timestamp (git) | Timestamp of the first commit (`git log --reverse --max-count=1`). | 2020-03-18 14:22:11 +0000 |
| commit_count | integer | Number of commits across all branches (`git rev-list --count --all`). | 1582 |
| branch_count | integer | Count of local and remote branches (`git branch -a`). | 41 |
| contributors_count | integer | Unique authors in history (`git shortlog -sne --all`). | 27 |
| repo_git_history_mb | float | Size of `.git` directory in MB via `du -sk`. | 134.8 |
| repo_bundle_mb | float | Size of the bundle file in MB. | 512.3 |
| repo_worktree_mb | float | Size of the working tree excluding `.git` (MB). | 92.5 |
| files | integer | File count from cloc (`SUM.nFiles`). | 1287 |
| loc | integer | Code plus comment lines from cloc (non-empty). | 442915 |
| avg_func_length | float | Average function length via Tree-sitter; 0 if grammars are unavailable. | 12.4 |
| docstring_ratio | float | Ratio of comment lines to code lines (`comment/code`). | 0.18 |
| duplication_ratio | float | Duplication estimate: `1 - unique_lines/total_lines` (0-1). | 0.27 |
| documentation_cnt | integer | Line count across all `README*` files in the repository root. | 245 |
| deepseek_token_count_all_commits | integer | Tokens for added lines across all commits; 0 if the tokenizer is not configured. | 12487221 |
| deepseek_token_count_last_commit | integer | Tokens for the current snapshot of allowed files; 0 without a tokenizer. | 487552 |

### Installing Tree-sitter grammars
```bash
uv run repo-metadata fetch-grammars --config-file repo_metadata.toml
```
Installs packages listed in `tree_sitter.language_packages` via `uv pip install`.

### Refreshing the extension allowlist
```bash
uv run repo-metadata refresh-allowed --config-file repo_metadata.toml
```
Populates `files.allowed_extensions` based on `tree_sitter.extension_language_map`.

## Logging
- Default level is `INFO`; adjust via `--log-level` (`DEBUG` is useful for Tree-sitter or tokenizer diagnostics).
- Logs are emitted to stdout with format `%(asctime)s | %(levelname)s | %(name)s | %(message)s`.

## Pipeline overview
- For each bundle:
  - Clone into a temporary directory with `GIT_LFS_SKIP_SMUDGE=1`.
  - Collect history (`git log`, `git rev-list`, branch and author counts).
  - Measure sizes: bundle, `.git`, working tree.
  - Detect the license from LICENSE/COPYING files.
  - Count README lines in the repository root.
  - Run `cloc --json` for file and line counts and language distribution.
  - Compute average function length via Tree-sitter on allowed files only.
  - Compute duplication as unique-line share.
- For tokens:
  - All commits: gather added lines from `git show --unified=0`.
  - Last snapshot: read content of all allowed files.
  - Count tokens in batches using the configured tokenizer.
- Rows are appended to CSV incrementally; existing repositories are not recomputed.

## Troubleshooting and tips
- If no `*.bundle` files are found, the CLI emits a warning and exits gracefully.
- If grammars are missing, use `--skip-tree-sitter` or install via `fetch-grammars`.
- If `transformers` or the tokenizer is absent, token counts remain zero; run `uv add transformers` and set `--tokenizer-id`.
- Pin `uv.lock` in version control for reproducibility.
- Ensure `cloc` is on PATH; otherwise, line metrics will be empty.

## Development
- Build check: `uv run python -m compileall src/repo_metadata_cli`.
- Entry point: `repo-metadata` (declared in `pyproject.toml`).
- Configure log verbosity with `--log-level` to surface diagnostics during development.
