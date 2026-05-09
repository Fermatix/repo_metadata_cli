#!/usr/bin/env bash
set -uo pipefail

INPUT_FILE="${1:-repos.txt}"
MIRRORS_DIR="${2:-./mirrors}"
BUNDLES_DIR="${3:-./tmp/doubletapp/bundles}"
OK_FILE="${4:-./gitlab_repos.txt}"
PARALLEL="${5:-8}"   # concurrent jobs; override via 5th arg or PARALLEL env var
PARALLEL="${PARALLEL:-8}"

mkdir -p "$MIRRORS_DIR" "$BUNDLES_DIR" "$(dirname "$OK_FILE")"
: > "$OK_FILE"

MIRRORS_DIR="$(cd "$MIRRORS_DIR" && pwd)"
BUNDLES_DIR="$(cd "$BUNDLES_DIR" && pwd)"
OK_FILE="$(cd "$(dirname "$OK_FILE")" && pwd)/$(basename "$OK_FILE")"

# ---------------------------------------------------------------------------
# Name helpers
# ---------------------------------------------------------------------------

safe_name() {
  local url="$1"
  local s path
  url="${url%.git}"
  s="$(echo "$url" | sed -E 's#^[a-zA-Z]+://##; s#:#/#; s#^[^@]+@##')"
  path="${s#*/}"
  path="${path#/}"
  path="$(echo "$path" | sed -E 's#/+#--#g')"
  path="$(echo "$path" | sed -E 's#[^A-Za-z0-9.-]+#-#g')"
  path="$(echo "$path" | sed -E 's#-+#-#g')"
  path="$(echo "$path" | sed -E 's#^[.-]+##; s#[.-]+$##')"
  [[ -z "$path" ]] && path="repo"
  [[ "$path" == *.git || "$path" == *.atom ]] && path="${path}-repo"
  echo "$path"
}

repo_only_name() {
  local url="$1"
  local s path base
  url="$(echo "$url" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"
  url="${url%/}"
  url="${url%.git}"
  s="$(echo "$url" | sed -E 's#^[a-zA-Z]+://##; s#:#/#; s#^[^@]+@##')"
  path="${s#*/}"
  path="${path#/}"
  base="${path##*/}"
  base="$(echo "$base" | sed -E 's#[^A-Za-z0-9.-]+#-#g')"
  base="$(echo "$base" | sed -E 's#-+#-#g')"
  base="$(echo "$base" | sed -E 's#^[.-]+##; s#[.-]+$##')"
  [[ -z "$base" ]] && base="repo"
  [[ "$base" == *.git || "$base" == *.atom ]] && base="${base}-repo"
  echo "$base"
}

# ---------------------------------------------------------------------------
# Semaphore — limits to $PARALLEL concurrent jobs
# ---------------------------------------------------------------------------

_SEM="$(mktemp -u)"
mkfifo "$_SEM"
exec 9<>"$_SEM"
rm -f "$_SEM"
for _i in $(seq 1 "$PARALLEL"); do printf ' ' >&9; done

acquire() { read -rn1 -u9; }
release() { printf ' ' >&9; }

# ---------------------------------------------------------------------------
# Per-repo worker (runs in a subshell)
# ---------------------------------------------------------------------------

process_repo() {
  local repo="$1"
  local log_prefix

  local mirror_name bundle_name repo_dir bundle_path
  mirror_name="$(safe_name "$repo")"
  bundle_name="$(repo_only_name "$repo")"
  repo_dir="$MIRRORS_DIR/$mirror_name.git"
  bundle_path="$BUNDLES_DIR/$bundle_name.bundle"
  log_prefix="[$bundle_name]"

  # Skip if bundle already exists and is non-empty
  if [[ -s "$bundle_path" ]]; then
    echo "$log_prefix already exists, skipping"
    release
    return 0
  fi

  echo "$log_prefix fetching…"

  if [[ ! -d "$repo_dir" ]]; then
    git init --bare "$repo_dir" >/dev/null 2>&1
    git -C "$repo_dir" remote add origin "$repo"
  else
    local current_url
    current_url="$(git -C "$repo_dir" remote get-url origin 2>/dev/null || true)"
    if [[ "$current_url" != "$repo" && -n "$repo" ]]; then
      git -C "$repo_dir" remote set-url origin "$repo"
    fi
  fi

  git -C "$repo_dir" config remote.origin.mirror true        || true
  git -C "$repo_dir" config --unset-all remote.origin.fetch  >/dev/null 2>&1 || true
  git -C "$repo_dir" config --add remote.origin.fetch "+refs/*:refs/*"
  git -C "$repo_dir" config --add remote.origin.fetch "+refs/merge-requests/*:refs/merge-requests/*" 2>/dev/null || true
  git -C "$repo_dir" config --add remote.origin.fetch "+refs/pull/*:refs/pull/*" 2>/dev/null || true

  if ! git -C "$repo_dir" fetch --force --prune --prune-tags origin 2>/dev/null; then
    echo "$log_prefix ⚠️  fetch failed, skipping"
    release
    return 1
  fi

  # Set HEAD
  local remote_head_ref fallback_head
  remote_head_ref="$(git -C "$repo_dir" ls-remote --symref origin HEAD 2>/dev/null \
    | awk '/^ref:/ {print $2; exit}')"
  if [[ -n "$remote_head_ref" ]]; then
    git -C "$repo_dir" symbolic-ref HEAD "$remote_head_ref" 2>/dev/null || true
  else
    fallback_head="$(git -C "$repo_dir" for-each-ref --format='%(refname)' refs/heads \
      | head -n1)"
    [[ -n "$fallback_head" ]] && git -C "$repo_dir" symbolic-ref HEAD "$fallback_head" 2>/dev/null || true
  fi

  if ! git -C "$repo_dir" show-ref --quiet 2>/dev/null; then
    echo "$log_prefix ⚠️  no refs (empty repo or no access), skipping"
    release
    return 1
  fi

  rm -f "$bundle_path"
  if git -C "$repo_dir" bundle create "$bundle_path" --all 2>/dev/null; then
    echo "$log_prefix ✅ done"
    # Atomic append — safe for lines < 512 bytes on macOS/Linux
    echo "$repo" >> "$OK_FILE"
  else
    echo "$log_prefix ⚠️  bundle create failed"
    release
    return 1
  fi

  release
}

export -f process_repo safe_name repo_only_name release
export MIRRORS_DIR BUNDLES_DIR OK_FILE

# ---------------------------------------------------------------------------
# Main loop — dispatch jobs with semaphore
# ---------------------------------------------------------------------------

total=0
while IFS= read -r repo; do
  [[ -z "${repo// }" ]] && continue
  [[ "$repo" =~ ^# ]]   && continue
  (( total++ )) || true
  acquire                        # blocks when PARALLEL slots are full
  process_repo "$repo" &
done < "$INPUT_FILE"

wait   # wait for all background jobs to finish
exec 9>&-

echo ""
echo "OK list: $OK_FILE"
echo "Processed $total repos with up to $PARALLEL parallel workers."
