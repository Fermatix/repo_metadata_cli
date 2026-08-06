# Metrics `pr_simple_pct`, `pr_standard_pct`, `pr_rich_pct`, `avg_loc_per_pr` (columns AX–BA)

Distribution of merged PR/MR sizes and the average PR size, computed
deterministically from the local VCS history. No LLM, no network calls.

## Unit of analysis

The size unit is one merged PR/MR when platform fingerprints are present in
commit messages, with two fallbacks otherwise:

1. **Platform fingerprints** (`basis = pr`) — real PRs/MRs recognised in the
   history across all refs:
   - GitHub merge commits: `Merge pull request #N …` — measured as the diff to
     the **first parent** of the merge commit;
   - GitHub squash merges: subject ending in `(#N)` — measured as the commit's
     own diff;
   - GitLab MR merge commits: body containing `See merge request …!N` —
     measured as the diff to the first parent.

   Units are deduplicated by `(platform, PR number)`; merge fingerprints are
   collected first, then squash fingerprints, both in newest-first log order —
   so when the same number appears twice, the most recent occurrence wins.
   Local/sync merges without a fingerprint are NOT counted in this basis.
2. **Merge commits** (`basis = merge`) — when no fingerprints exist (rewritten
   history, mirrors, non-standard messages): every merge commit on the
   checked-out line, measured as the diff to the first parent.
3. **Plain commits** (`basis = commit`) — when there are no merge commits
   either: every non-merge commit on the checked-out line (squash/fast-forward
   workflows: commit ≈ PR), measured as the commit's own diff.

At most **400** units are analysed (newest first).

## Size buckets

`size = additions + deletions` (rename detection enabled for git via `-M`):

| Bucket | Condition | Column |
|---|---|---|
| simple | `size <= 50` | `pr_simple_pct` |
| standard | `51 <= size <= 300` | `pr_standard_pct` |
| rich | `size > 300` | `pr_rich_pct` |

```
pr_<bucket>_pct = round(100 * bucket_count / units_total)
avg_loc_per_pr  = round(total_changed_lines / units_total)
```

`round` is Python's built-in (banker's rounding), matching the reference
implementation this metric was ported from.

## Gating on `total_pr_count`

The distribution describes PRs, so it is gated on the pipeline's **effective**
`total_pr_count` (column P: the PR-cache value when trusted, otherwise the
git-log fingerprint count). When that count is 0 the four columns are 0 — a
commit-size distribution is not passed off as PR statistics.

## Zero semantics (agreed, not an error)

All four columns are `0` when:

- `total_pr_count == 0`, or
- the history has fewer than two commits and no merges, or
- no unit could be formed at all.

An actual computation **error** instead leaves the cell empty and logs a
warning naming the repository and the metric; empty cells are recomputed on
the next run (see `docs`-level notes on CSV migration in the README).

## VCS support

Both backends implement the same methodology through the shared VCS interface
(`pr_fingerprint_units` / `merge_unit_revs` / `commit_unit_revs` /
`unit_changed_lines`):

- **Git** — `git log --all` for fingerprints, `git log` (checked-out line) for
  fallbacks, `git diff --shortstat -M rev^1 rev` / `git show --shortstat -M`
  for sizes. Byte-for-byte parity with the reference implementation is pinned
  by golden tests.
- **Mercurial** — `hg log -r 'reverse(merge())'` (fingerprints across the
  repo), `reverse(ancestors(.) and merge())` / `reverse(ancestors(.) and not
  merge())` for fallbacks, `hg diff -c REV --stat` for sizes (diff to the
  first parent for merges). Same thresholds, cap, rounding and zero semantics.

A plain directory without VCS history yields the agreed zeros.
