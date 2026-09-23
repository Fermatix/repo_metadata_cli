# Repository Metadata CLI

Collect repository metadata locally and export one CSV row per repository.

## Required: Quickstart

Read these four steps to collect and check your metadata. Everything after the
**Optional reference** divider is for additional inputs, settings and metric definitions.

### 1. Install dependencies

Use macOS or Linux. Git, `scc` (line counts) and `jscpd` (duplication) are required.
The Python package supports Python 3.10+; the commands below use Python 3.12,
which `uv` installs if needed.

**macOS**, with [Homebrew](https://brew.sh/) installed:

```bash
brew install git uv scc node
npm install --global jscpd
```

**Ubuntu / Debian**:

```bash
sudo apt-get update
sudo apt-get install -y git curl nodejs npm
curl -LsSf https://astral.sh/uv/install.sh | sh
. "$HOME/.local/bin/env"
npm install --global --prefix "$HOME/.local" jscpd
mkdir -p "$HOME/.local/bin"
scc_arch="$(uname -m)"
case "$scc_arch" in aarch64) scc_arch=arm64 ;; esac
curl -fL "https://github.com/boyter/scc/releases/latest/download/scc_Linux_${scc_arch}.tar.gz" \
  | tar -xz -C "$HOME/.local/bin" scc
export PATH="$HOME/.local/bin:$PATH"
```

**Then, on either platform**:

```bash
git clone https://github.com/Fermatix/repo_metadata_cli.git
cd repo_metadata_cli
uv sync --locked --python 3.12
git --version
scc --version
jscpd --version
```

Run subsequent commands from this directory so they can find
`repo_metadata.toml`. `uv run` uses the project environment; activation is unnecessary.

### 2. Prepare the repository list

Create `repos.txt` with one Git SSH or HTTPS URL per line:

```text
# Blank lines and lines starting with # are ignored

git@git.example.com:group/service-api.git
https://github.com/example-org/mobile-app.git

# Without an SSH key, include your username and token in the HTTPS URL
https://username:TOKEN@git.example.com/group/legacy-service.git
```

Replace the examples with your repositories and end the last line with a newline.

For token environment variables or PR/MR data from a hosting API, see
[Access and PR data](#access-and-pr-data).

### 3. Run collection

Choose a new run directory for each batch or fresh recalculation:

```bash
mkdir -p runs/first
uv run repo-metadata metadata repos.txt \
  --output-csv runs/first/metadata.csv \
  --bundles-dir runs/first/bundles \
  --mirrors-dir runs/first/mirrors \
  --ok-file runs/first/fetched.txt \
  > runs/first/run.log 2>&1
```

This creates local mirrors and bundles, measures the repositories and writes the
CSV. Git code metrics use the branch with the most recent commit, which can differ
from the default branch.

### 4. Check and collect the result

The result is **`runs/first/metadata.csv`**. Share or import that file after checking:

- Every intended repository has exactly one row; check `repo_url`, `repo_org` and
  `repo_name` for missing repositories or duplicates.
- `runs/first/run.log` contains no unresolved fetch, clone or metric errors.
- Unexpected zero LOC, history or duplication values have been investigated.

A successful exit alone does not prove that every listed repository was fetched.
`fetched.txt` records newly fetched repositories, not completion of CSV generation.
No data is uploaded unless you explicitly add `--upload`.

Rerun the same command to resume an interrupted batch. Existing bundles and
completed CSV rows are reused. To measure updated source code or use different
settings, use a new run directory; resume does not refresh completed rows.

---

## Optional reference

The collection workflow above is complete. Read the following sections only when
you need another input format, API enrichment, upload or metric details.

### Access and PR data

For HTTPS URLs without embedded credentials, use `GITLAB_TOKEN` for GitLab access
(`read_repository` for fetching and `read_api` for MR data).
Use `GITHUB_TOKEN` for GitHub HTTPS access and PR data,
with access to the repositories being measured. SSH URLs use your SSH setup.

To add API-derived PR/MR counts, set the appropriate token in your environment
and add `--pr-cache runs/first/pr_cache.json` to the collection command. For a
self-hosted GitLab, also add:

```text
--gitlab-base-url https://git.example.com/api/v4
```

Automatic enrichment needs **both a `.txt` input and `--pr-cache` plus a token**.
A local filesystem path does not identify a hosting project for enrichment; use
hosting URLs when collecting API data. Without a usable API cache,
`total_pr_count` and `merged_pr_count` fall back to merge/squash fingerprints in
history, while `reviewed_pr_count` is 0.

For an existing bundle collection, prepare a cache separately using the original
hosting URLs, then pass `--pr-cache` to `metadata`:

```bash
uv run repo-metadata enrich-prs hosting-repos.txt \
  --bundles-dir runs/first/bundles \
  --cache-file runs/first/pr_cache.json
```

Add the same `--gitlab-base-url` here for a self-hosted instance. Cache keys must
match bundle filenames. Existing nonzero entries may be reused; use a new cache
and output CSV when you need refreshed counts.

### Other inputs

Local Git paths and Mercurial sources can also be listed in `repos.txt`:

```text
/home/user/repos/internal-tool

# Mercurial: use the hg+ prefix
hg+/home/user/repos/legacy-billing
hg+https://hg.example.org/old-project
```

For local Git paths, use clones containing the branches and history you want
measured. The list-based workflow reads these clones without checking them out
or changing their files.

| Input passed to `metadata` | Behavior |
|---|---|
| `.txt` file | Fetch the listed repositories into mirrors and bundles, then measure the bundles directory. |
| Directory containing `*.bundle` or `*.hgbundle` | Find bundles recursively and measure them. Other directories are not treated as separate repositories. |
| Directory without bundles | Treat each immediate, non-hidden subdirectory as one repository. Plain source folders work without VCS history. |

For existing bundles:

```bash
uv run repo-metadata metadata /data/bundles --output-csv bundle-metadata.csv
```

For source folders, pass their **parent directory**, for example `/data/projects`
containing `project-a/` and `project-b/`. Without history, commit and PR metrics are
0 and history fingerprints are empty. If these folders are Git working copies,
directory mode force-checks out the latest-commit branch **in place**, which can
discard uncommitted changes. Use the `.txt` workflow for working clones.

Mercurial accepts `hg+` URLs/paths or an `*.hgbundle`, and can be mixed with Git
inputs. For Mercurial, include `mercurial` in the `brew install` or
`apt-get install` command from step 1.
`meta_non_merge_commit_count` is Git-only and is 0 for Mercurial.

### Metrics

The CSV includes repository identity, LOC, languages, Git/Mercurial history,
PR/MR statistics, CI, tests, documentation and code structure. Pricing fields
(`quoted_price`, `pricing_unit`, `unit_rate`) are empty placeholders.

| Field | Meaning |
|---|---|
| `raw_loc` | `scc` total lines, including comments and blank lines, without the configured dependency exclusions. |
| `logical_loc` | `scc` code lines excluding configured dependency/build directories; generated code remains included. |
| `autogen_loc` | Generated code within the `logical_loc` file set. |
| `dependency_dir_loc` | Code lines in configured dependency directories. |
| `clean_logical_loc` | Code lines with broader directory exclusions and filtering of data/config formats and database dumps; handwritten markup stays included. |
| `clean_handwritten_loc`, `autogen_in_clean_loc` | Handwritten and generated portions of `clean_logical_loc`; their sum equals it. Do not subtract `autogen_loc` from this different file set. |
| `primary_language`, `lang_distribution` | Programming-language mix; `full_lang_distribution` also includes non-code formats. |
| `commit_count` | Commits across all refs, including merges. |
| `metadata_commit_hash`, `metadata_branch_name` | Commit and branch selected for measurement. |
| `total_pr_count`, `merged_pr_count` | All-state and merged counts from a usable API cache; without it, both are merged PRs detected in history. |
| `test_coverage_pct` | Static test-code share, not runtime coverage. No tests are executed. |
| `untested_files_pct` | Share of eligible code files that are not test files, not a measurement of which files tests cover. |
| `functions_count`, `classes_count` | Tree-sitter counts of function/method and class-like declarations. `--skip-tree-sitter` disables these and other AST metrics. |

Detailed definitions:
[PR size distribution](docs/metrics/pr_size_distribution.md),
[test estimates](docs/metrics/test_coverage_pct.md),
[AST counts](docs/metrics/ast_symbol_counts.md),
[dependency LOC](docs/metrics/dependency_dir_loc.md).

### Fixed-recipe metrics

The `meta_*` fields use separate external-tool recipes, unaffected by TOML metric
settings or `--exclude-dir`. Commands run in the measured working tree:

| Field | Recipe |
|---|---|
| `meta_logical_loc` | `scc . --format json`: sum of `Code` across languages. |
| `meta_logical_loc_excl_vendor` | `scc . --gen --by-file --exclude-dir vendor,node_modules,dist,build,generated,migrations --format json`: sum of all `Files[].Code`. |
| `meta_generated_loc` | Same vendor-excluded report, summing only files with `Generated == true`. |
| `meta_non_authored_loc` | `min(max(0, meta_logical_loc - meta_logical_loc_excl_vendor) + meta_generated_loc, meta_logical_loc)`. |
| `meta_duplication_ratio` | `jscpd . --min-tokens 50 --min-lines 5 --reporters json --output <temporary-directory>`: `statistics.total.percentage / 100`. |
| `meta_non_merge_commit_count` | Count lines from `git log --oneline --no-merges` on the measured HEAD, excluding lines containing `revert` case-insensitively. |

External-tool failures can produce zero values with warnings. Review the run log
before interpreting zero as a measured absence of code, duplication or history.

### Configuration and resume

The default configuration is [repo_metadata.toml](repo_metadata.toml). Use
`--config-file /path/to/repo_metadata.toml` when running from another directory.

`--exclude-dir` is repeatable. A bare name matches at any depth; a multi-segment
path matches those consecutive segments. Matching is case-sensitive. It changes
regular code metrics, including `logical_loc`, language shares, AST counts and
test estimates; `raw_loc` and the fixed `meta_*` recipes retain their own scope.
Record custom exclusions when sharing CSVs, since the results differ from a
default run.

Older CSVs are migrated in place, preserving unknown columns and existing rows.
Missing new fields are backfilled only for repositories present in the input.
For legacy files without `meta_generated_loc`, the old `meta_non_authored_loc`
is interpreted as generated-only LOC, `meta_loc_with_generated` is renamed to
`meta_logical_loc_excl_vendor`, and the current `meta_non_authored_loc` is derived
from the stored LOC fields. Keep a copy before reusing an older CSV.

Keep bundle directories specific to a batch: all bundles found there are measured,
including any left over from an earlier list. Large inputs need space for mirrors,
bundles and temporary working trees; set `TMPDIR` to an existing directory on a
larger volume if necessary.

### Optional upload

To upload during collection, set `CRM_LOGIN` and `CRM_PASSWORD` in your environment
and add `--upload` to the command. `--crm-url` selects the destination; use
`uv run repo-metadata metadata --help` to see the default. The CSV is retained
locally. The command reports created/updated rows and any import errors.

Upload runs before the final incomplete-run check, so a partial CSV can be sent
with `--upload`. For a run that needs review, collect locally and check the result
before importing it through your usual workflow.

### Troubleshooting

| Symptom | Action |
|---|---|
| `repo-metadata` not found | Run it as `uv run repo-metadata` from the cloned project. |
| Missing `scc` or `jscpd` | Install the missing tool using step 1; collection stops before fetching. `--allow-missing-jscpd` explicitly permits zeroed duplication fields and is unsuitable for a complete collection. |
| Missing `extension_language_map` | Run from the project directory or pass `--config-file` pointing to its TOML. |
| Fetch/authentication errors | Check the URL, token or SSH access. Check the CSV against the entire input list, even after exit code 0. |
| Zero PR/review counts | Check token access, `--pr-cache` and the GitLab API base URL. Filesystem paths alone cannot supply API counts. |
| `produced NO row`, exit code 1 | A fetched repository could not be materialized. Fix the logged cause and resume; the existing CSV may be partial. |
| `measured from a repaired working tree` | Some paths rejected by the OS were restored under sanitized names. Review the logged renames if path-dependent metrics matter. |

The optional [CSV validator](src/repo_metadata_cli/scripts/validate_csv.py) checks
formats and invariants, but currently reads literal `None` enum values as pandas
`NaN` and can report false errors. Inspect those cells in the CSV; do not use the
validator's exit code alone as the acceptance check.

For all available options: `uv run repo-metadata --help` and
`uv run repo-metadata metadata --help`.
