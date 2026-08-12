"""Recovery of tracked files the operating system refused to check out.

Some repositories contain paths that are perfectly legal in git but impossible
to create on the machine running the tool — most often on Windows, where a
backslash inside a file name is a directory separator, ``: * ? " < > |`` are
forbidden, trailing dots/spaces are stripped and names like ``CON``/``NUL`` are
reserved.  ``git checkout`` then writes everything it can and fails on those
paths, so a plain ``git clone`` exits non-zero and the repository used to be
dropped from the run entirely.

The content is still in the object database, so it is recovered here: every
tracked-but-missing path is read with ``git cat-file`` and written under a
sanitized name.  Only the NAME changes (the extension is preserved, so language
detection and LOC counting are unaffected); the bytes are identical.

Forcing git instead (``core.protectNTFS=false``) is deliberately NOT done: a
file named ``..\\..\\x`` would then be written OUTSIDE the working tree, and
these repositories are third-party input.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# Characters that cannot appear in a file name on Windows (backslash included:
# it is a path separator there) plus control characters.
_HOSTILE_CHARS = re.compile(r'[\\:*?"<>|\x00-\x1f]')

# Device names reserved by Windows, with or without an extension.
_RESERVED_STEMS = {"con", "prn", "aux", "nul"} | {
    f"{prefix}{i}" for prefix in ("com", "lpt") for i in range(1, 10)
}

_MAX_BLOB_BYTES = 64 * 1024 * 1024  # skip absurd blobs; scc ignores them anyway


@dataclass
class CheckoutRepair:
    """Outcome of one repair pass over a repository."""

    restored: int = 0
    renamed: List[Tuple[str, str]] = field(default_factory=list)
    failed: List[str] = field(default_factory=list)

    @property
    def touched(self) -> bool:
        return bool(self.restored or self.failed)


def sanitize_rel_path(rel_path: str) -> str:
    """Map a repo-relative git path to one every filesystem accepts.

    Segment-wise: hostile characters become ``_``, trailing dots/spaces are
    dropped, reserved device names get an ``_`` prefix.  The suffix survives, so
    ``Sources\\View.swift`` becomes ``Sources_View.swift`` and is still Swift.
    """
    parts: List[str] = []
    for segment in rel_path.split("/"):
        if not segment or segment in (".", ".."):
            continue
        cleaned = _HOSTILE_CHARS.sub("_", segment).rstrip(" .") or "_"
        stem = cleaned.split(".", 1)[0].lower()
        if stem in _RESERVED_STEMS:
            cleaned = f"_{cleaned}"
        parts.append(cleaned)
    return "/".join(parts)


def _unique_target(repo_dir: Path, rel_path: str) -> Optional[Path]:
    """Resolve ``rel_path`` inside ``repo_dir``, avoiding collisions and escapes."""
    root = repo_dir.resolve()
    candidate = (repo_dir / rel_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None  # sanitization still pointed outside the tree — refuse
    if ".git" in candidate.relative_to(root).parts[:-1]:
        return None
    if not candidate.exists():
        return candidate
    stem, suffix = candidate.stem, candidate.suffix
    for n in range(1, 1000):
        alt = candidate.with_name(f"{stem}_{n}{suffix}")
        if not alt.exists():
            return alt
    return None


def _missing_tracked_paths(repo_dir: Path) -> List[str]:
    """Tracked paths that are absent from the working tree (git's own view)."""
    result = subprocess.run(
        ["git", "-C", str(repo_dir), "ls-files", "-z", "--deleted"],
        capture_output=True,
    )
    if result.returncode != 0:
        return []
    return [p for p in result.stdout.decode("utf-8", "replace").split("\0") if p]


def restore_rejected_files(repo_dir: Path) -> CheckoutRepair:
    """Write every tracked-but-missing file back, under a sanitized name.

    Returns a report; an empty one means the working tree was already complete.
    """
    report = CheckoutRepair()
    for rel_path in _missing_tracked_paths(repo_dir):
        blob = subprocess.run(
            ["git", "-C", str(repo_dir), "cat-file", "blob", f"HEAD:{rel_path}"],
            capture_output=True,
        )
        if blob.returncode != 0:
            report.failed.append(rel_path)
            continue
        if len(blob.stdout) > _MAX_BLOB_BYTES:
            report.failed.append(rel_path)
            continue
        safe_rel = sanitize_rel_path(rel_path)
        target = _unique_target(repo_dir, safe_rel) if safe_rel else None
        if target is None:
            report.failed.append(rel_path)
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(blob.stdout)
        except OSError as exc:
            logger.debug("Could not restore %s as %s: %s", rel_path, safe_rel, exc)
            report.failed.append(rel_path)
            continue
        report.restored += 1
        written = target.resolve().relative_to(repo_dir.resolve()).as_posix()
        if written != rel_path:
            report.renamed.append((rel_path, written))
    return report
