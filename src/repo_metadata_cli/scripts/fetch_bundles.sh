#!/usr/bin/env bash
set -euo pipefail

INPUT_FILE="${1:-repos.txt}"
MIRRORS_DIR="${2:-./mirrors}"
BUNDLES_DIR="${3:-./tmp/doubletapp/bundles}"
OK_FILE="${4:-./gitlab_repos.txt}"

mkdir -p "$MIRRORS_DIR" "$BUNDLES_DIR"
: > "$OK_FILE"

MIRRORS_DIR="$(cd "$MIRRORS_DIR" && pwd)"
BUNDLES_DIR="$(cd "$BUNDLES_DIR" && pwd)"
OK_FILE="$(cd "$(dirname "$OK_FILE")" && pwd)/$(basename "$OK_FILE")"

safe_name() {
  # Produces GitLab-safe and filesystem-safe name WITHOUT underscores.
  # Examples:
  #   https://github.com/org/repo.git            -> org--repo
  #   git@github.com:org/repo.git               -> org--repo
  #   https://gitlab.com/group/sub/repo         -> group--sub--repo
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

  if [[ -z "$path" ]]; then
    path="repo"
  fi

  if [[ "$path" == *.git || "$path" == *.atom ]]; then
    path="${path}-repo"
  fi

  echo "$path"
}

repo_only_name() {
  # Extract ONLY the last path segment ("repo") and make it filesystem-safe.
  # Examples:
  #   https://github.com/org/repo.git          -> repo
  #   git@github.com:org/repo.git             -> repo
  #   https://gitlab.com/group/sub/repo       -> repo
  local url="$1"
  local s path base

  # trim spaces
  url="$(echo "$url" | sed -E 's/^[[:space:]]+//; s/[[:space:]]+$//')"

  # drop trailing "/" and trailing ".git"
  url="${url%/}"
  url="${url%.git}"

  # normalize (remove scheme, convert ":" -> "/", drop userinfo)
  s="$(echo "$url" | sed -E 's#^[a-zA-Z]+://##; s#:#/#; s#^[^@]+@##')"

  # drop host
  path="${s#*/}"
  path="${path#/}"

  # take last segment
  base="${path##*/}"

  # sanitize
  base="$(echo "$base" | sed -E 's#[^A-Za-z0-9.-]+#-#g')"
  base="$(echo "$base" | sed -E 's#-+#-#g')"
  base="$(echo "$base" | sed -E 's#^[.-]+##; s#[.-]+$##')"

  if [[ -z "$base" ]]; then
    base="repo"
  fi

  if [[ "$base" == *.git || "$base" == *.atom ]]; then
    base="${base}-repo"
  fi

  echo "$base"
}

while IFS= read -r repo || [[ -n "$repo" ]]; do
  [[ -z "${repo// }" ]] && continue
  [[ "$repo" =~ ^# ]] && continue

  mirror_name="$(safe_name "$repo")"
  bundle_name="$(repo_only_name "$repo")"

  repo_dir="$MIRRORS_DIR/$mirror_name.git"
  bundle_path="$BUNDLES_DIR/$bundle_name.bundle"

  echo "=== $mirror_name ==="
  echo "repo: $repo"
  echo "bundle: $(basename "$bundle_path")"

  if [[ ! -d "$repo_dir" ]]; then
    echo "→ init bare mirror: $repo_dir"
    git init --bare "$repo_dir" >/dev/null
    git -C "$repo_dir" remote add origin "$repo"
  else
    echo "→ mirror exists, updating"
    current_url="$(git -C "$repo_dir" remote get-url origin 2>/dev/null || true)"
    if [[ "$current_url" != "$repo" && -n "$repo" ]]; then
      echo "→ refresh remote URL (was: ${current_url:-<none>})"
      git -C "$repo_dir" remote set-url origin "$repo"
    fi
  fi

  # fetch EVERYTHING the remote advertises
  git -C "$repo_dir" config remote.origin.mirror true || true
  git -C "$repo_dir" config --unset-all remote.origin.fetch >/dev/null 2>&1 || true
  git -C "$repo_dir" config --add remote.origin.fetch "+refs/*:refs/*"
  git -C "$repo_dir" config --add remote.origin.fetch "+refs/merge-requests/*:refs/merge-requests/*" || true
  git -C "$repo_dir" config --add remote.origin.fetch "+refs/pull/*:refs/pull/*" || true

  echo "→ fetch all refs (force/prune)"
  git -C "$repo_dir" fetch --force --prune --prune-tags origin

  # Ensure bundle has a valid HEAD
  remote_head_ref="$(git -C "$repo_dir" ls-remote --symref origin HEAD 2>/dev/null | awk '/^ref:/ {print $2; exit}')"
  if [[ -n "$remote_head_ref" ]]; then
    git -C "$repo_dir" symbolic-ref HEAD "$remote_head_ref" || true
  else
    fallback_head="$(git -C "$repo_dir" for-each-ref --format='%(refname)' refs/heads | head -n1)"
    [[ -n "$fallback_head" ]] && git -C "$repo_dir" symbolic-ref HEAD "$fallback_head" || true
  fi

  if ! git -C "$repo_dir" show-ref --quiet; then
    echo "⚠️ no refs fetched (empty repo or no access). skip bundle."
    echo
    continue
  fi

  echo "→ create bundle: $bundle_path"
  rm -f "$bundle_path"
  git -C "$repo_dir" bundle create "$bundle_path" --all

  echo "$repo" >> "$OK_FILE"

  echo "✅ done"
  echo
done < "$INPUT_FILE"

echo "OK list: $OK_FILE"
