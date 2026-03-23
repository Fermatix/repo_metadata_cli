# repo-metadata-cli

A command-line utility for extracting repository metadata from Git bundle files. It is designed for dataset curation and code quality assessment: it identifies licenses, characterizes the technology stack, quantifies history and language distribution, measures documentation and duplication, estimates average function length via Tree-sitter, and optionally performs tokenization for the branch that contains the most recent commit (diff and snapshot).

The `metadata` command accepts two input types:
- **Bundle directory** — point it at a directory of pre-existing `*.bundle` files.
- **Repository list** — provide a `.txt` file with one repository URL per line; the tool mirrors each repository, creates bundle files, and immediately computes metadata.

## Capabilities
- Fetches remote repositories and creates `*.bundle` files from a plain-text URL list (supports public and private repos via token) — or works with a pre-built bundle directory.
- Processes full datasets of `*.bundle` files in a single pass.
- History metrics: creation date, counts of commits and branches, sizes of `.git` and the working tree.
- Code quality: `cloc` counts (files, code/comment lines, optionally filtered languages), language and extension distributions, duplication ratio, average function length via Tree-sitter, README volume.
  - `raw_loc`: total code + comment lines across all languages (unfiltered cloc); `loc` respects `include_languages`/`--include-lang` when provided.
- License discovery: fast detection via LICENSE/COPYING files.
- Tokenization: tokens for the latest commit and snapshot of the branch with the most recent commit (HuggingFace tokenizer).
- Configuration is entirely TOML-based; no hard-coded language lists.

## Requirements
- Python 3.10+
- [uv](https://github.com/astral-sh/uv) as the environment and dependency manager
- Utilities: `git`, `cloc`, `bash` (required only for `from-repos`)
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
  - `include_languages`: optional list of language names to pass to cloc (`--include-lang`). When set, LOC and language distribution are computed only for these languages (overridable via `--include-lang` CLI flag).
  - `excluded_extensions`: optional list of extensions passed to cloc as `--exclude-ext` for LOC counting (metadata command).
- `[tree_sitter]`
  - `language_packages`: Python packages with grammars installable via `fetch-grammars`.
  - `extension_language_map`: mapping from file extension to language (keys normalized to lowercase with a dot).
  - `lang_func_node_types`: node types considered functions when computing average length.
  - `vendor_dir` / `language_repo_map`: optional local grammar paths when building from source.
- `[tokens]` (optional)
  - `tokenizer_id`: default Hugging Face tokenizer id for `repo-metadata tokens` (fallback: `$TOKENIZER_ID` env var).
  - `parallelism`: boolean to set `TOKENIZERS_PARALLELISM` (defaults to `false` if unspecified).
  - `max_length`: integer to override `tokenizer.model_max_length` and suppress long-sequence warnings during counting.

## Core commands
All commands accept `--config-file` (path to TOML) and `--log-level` for logging control.

### Metadata (no tokens)

**From a bundle directory:**
```bash
uv run repo-metadata metadata /path/to/dataset \
  --output-csv repo_metadata.csv \
  --config-file repo_metadata.toml
```

**From a repository list (fetch + analyze in one step):**
```bash
uv run repo-metadata metadata repos.txt \
  --output-csv repo_metadata.csv \
  --config-file repo_metadata.toml
```

- When `dataset_path` is a directory, scans all `*.bundle` files inside it.
- When `dataset_path` is a `.txt` file, mirrors each repository URL, creates bundle files, and then runs the analysis. Lines beginning with `#` and blank lines are ignored.
- Working tree metrics (cloc, duplication, avg_func_length, README, language distribution) are computed on the branch that contains the most recent commit.
- Use `--include-lang=Python,TypeScript` to restrict cloc/LOC to those languages (overrides `[files].include_languages`).
- LOC counting uses cloc with `--no-autogen --skip-win-hidden` and applies `[files].excluded_extensions` as `--exclude-ext`.
- The `--skip-tree-sitter` flag disables average function length computation.

**Options for `.txt` mode:**
| Option | Default | Description |
| --- | --- | --- |
| `--bundles-dir` | `./tmp/bundles` | Where to write fetched `*.bundle` files. |
| `--mirrors-dir` | `./tmp/mirrors` | Where to keep bare-mirror clones. |
| `--ok-file` | `./tmp/fetched_repos.txt` | File that records successfully fetched URLs. |
| `--gitlab-token` / `$GITLAB_TOKEN` | — | Personal access token for private repositories. |

**Example `repos.txt`:**
```
# public repos
https://github.com/org/repo-a.git
https://github.com/org/repo-b.git

# private repo (requires GITLAB_TOKEN)
https://gitlab.com/company/private-repo.git
```

**Private repositories:**
```bash
# via flag
uv run repo-metadata metadata repos.txt --gitlab-token glpat-xxxxxxxxxxxx

# via environment variable
GITLAB_TOKEN=glpat-xxxxxxxxxxxx uv run repo-metadata metadata repos.txt
```

### Tokens
```bash
uv run repo-metadata tokens /path/to/dataset \
  --output-csv repo_tokens.csv \
  --config-file repo_metadata.toml \
  --tokenizer-id deepseek-ai/deepseek-coder-6.7b-base
```
- `--by-partners-folders` also applies to token counting; otherwise, bundles are read directly from `dataset_dir`.
- If `--tokenizer-id` is omitted and `TOKENIZER_ID` is not set, token counts are skipped.
- Added-line tokens are collected only for the most recent commit (tip of the freshest branch), and snapshot tokens use that branch's working tree.
- The tokenizer is resolved via `transformers`; without the package or internet access, tokenization is skipped.
- Parallelism in `tokenizers` is disabled by default (`TOKENIZERS_PARALLELISM=false`) to suppress fork warnings; override the env var or `[tokens].parallelism` in the TOML.
- To silence "sequence length longer than the specified maximum" warnings during token counting, set `[tokens].max_length` to a higher value; counting does not truncate content.

### Merging tables
```bash
uv run repo-metadata merge repo_metadata.csv repo_tokens.csv \
  --output-csv repo_metadata_with_tokens.csv
```

## Output fields

| field_name | type | description | examples_or_rules |
| --- | --- | --- | --- |
| repo_id | string (UUID) | Primary key; randomly generated per repository during metadata extraction. | c2f9d1e8-9a41-4f72-9a8b-1f0f4f12e6a3 |
| partner | string | Partner folder name when `--by-partners-folders` is enabled; empty otherwise. | acme-corp |
| repo_name | string | Repository name (bundle stem); used as the key for `merge`. | openai/gym |
| languages | stringified JSON | Language distribution by share of LoC; JSON of the form `{lang: share}`. | {"Python":0.72,"C++":0.18} |
| extensions | stringified JSON | Extension distribution by share of LoC (after `include_languages`/`--include-lang` filters); JSON of the form `{ext: share}`. | {".py":0.82,".ts":0.18} |
| stack | string | Human-readable top 3 languages with percentages. | Python (72%), C++ (18%), C (6%) |
| license_type | enum | Detected root license: MIT, APACHE-2.0, GPL, GPL-3.0, BSD, MPL-2.0, UNLICENSE, UNKNOWN. | MIT |
| created_at | timestamp (git) | Timestamp of the first commit (`git log --reverse --max-count=1`). | 2020-03-18 14:22:11 +0000 |
| commit_count | integer | Number of commits on the branch with the most recent commit (`git rev-list --count <latest-branch>`). | 1582 |
| branch_count | integer | Count of local and remote branches (`git branch -a`). | 41 |
| contributors_count | integer | Unique authors in history (`git shortlog -sne --all`). | 27 |
| repo_git_history_mb | float | Size of `.git` directory in MB via `du -sk`. | 134.8 |
| repo_bundle_mb | float | Size of the bundle file in MB. | 512.3 |
| repo_worktree_mb | float | Size of the working tree excluding `.git` (MB). | 92.5 |
| files | integer | File count from cloc (`SUM.nFiles`). | 1287 |
| loc | integer | Code plus comment lines from cloc (non-empty), filtered by `include_languages`/`--include-lang` if provided. | 442915 |
| raw_loc | integer | Code plus comment lines from cloc without language filters. | 512000 |
| avg_func_length | float | Average function length via Tree-sitter; 0 if grammars are unavailable. | 12.4 |
| docstring_ratio | float | Ratio of comment lines to code lines (`comment/code`). | 0.18 |
| duplication_ratio | float | Duplication estimate: `1 - unique_lines/total_lines` (0-1). | 0.27 |
| documentation_cnt | integer | Line count across all `README*` files in the repository root. | 245 |
| deepseek_token_count_all_commits | integer | Tokens for added lines in the latest commit of the freshest branch; 0 if the tokenizer is not configured. | 12487221 |
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

### `metadata` (`.txt` mode: fetch + analyze)
1. Read repository URLs from the input file (skip blank lines and `#` comments).
2. For each URL: init or update a bare-mirror clone under `--mirrors-dir`, fetch all refs, create a `*.bundle` file under `--bundles-dir`.
3. Run the standard metadata pipeline on the populated bundles directory.

### `metadata` (directory mode) / `tokens`
- For each bundle:
  - Clone into a temporary directory with `GIT_LFS_SKIP_SMUDGE=1`.
  - Select the branch with the most recent commit and check it out.
  - Collect history (`git log`, `git rev-list` on the selected branch, branch and author counts).
  - Measure sizes: bundle, `.git`, working tree (on that branch).
  - Detect the license from LICENSE/COPYING files.
  - Count README lines in the repository root.
  - Run `cloc --json` twice: once unfiltered for `raw_loc`, and once with `--by-file-by-lang` respecting include_languages/`--include-lang` for `loc`, language, and extension distributions.
  - Compute average function length via Tree-sitter on allowed files only.
  - Compute duplication as unique-line share.
- For tokens:
  - Latest commit: gather added lines from `git show --unified=0` on the freshest branch tip.
  - Snapshot: read content of all allowed files on that branch.
  - Count tokens in batches using the configured tokenizer.
- Rows are appended to CSV incrementally; existing repositories are not recomputed.

## Troubleshooting and tips
- If no `*.bundle` files are found, the CLI emits a warning and exits gracefully.
- If grammars are missing, use `--skip-tree-sitter` or install via `fetch-grammars`.
- If `transformers` or the tokenizer is absent, token counts remain zero; run `uv add transformers` and set `--tokenizer-id`.
- Pin `uv.lock` in version control for reproducibility.
- Ensure `cloc` is on PATH; otherwise, line metrics will be empty.
- If `metadata` with a `.txt` file fails on a repository, check whether `$GITLAB_TOKEN` / `--gitlab-token` is set for private repos and that the URL is accessible from the machine running the tool.
- To re-run analysis on already-fetched bundles without re-cloning, pass the bundles directory directly instead of the `.txt` file.

## Development
- Build check: `uv run python -m compileall src/repo_metadata_cli`.
- Entry point: `repo-metadata` (declared in `pyproject.toml`).
- Configure log verbosity with `--log-level` to surface diagnostics during development.
