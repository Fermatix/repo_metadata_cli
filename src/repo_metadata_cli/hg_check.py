"""Fail-fast check for the external `hg` binary — only when it is needed.

Mercurial repositories yield every history column through ``hg`` (commit_count,
contributors_count, branch_count, created_at, PR counts and sizes, fork_pct,
issue_tracker and all commit-hash fingerprints).  With the binary absent those
commands returned nothing and the pipeline silently wrote zeros/blanks, which
is indistinguishable from a repository that genuinely has no history — one
partner delivered 23 Mercurial repositories that way.

Unlike scc, ``hg`` is NOT always required: a run consisting solely of git
repositories must not be blocked by it.  The check therefore inspects the input
first (``dataset_needs_hg``) and only then insists.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from .vcs.detect import detect_vcs_from_url

logger = logging.getLogger(__name__)

MISSING_MESSAGE = """\
Не найден hg — клиент Mercurial.

Во входных данных есть Mercurial-репозитории, а без hg все метрики истории
(число коммитов, авторы, ветки, дата создания, PR и их размеры, хэши коммитов)
молча получились бы нулевыми или пустыми, поэтому расчёт не запущен.

Варианты:
  1) Запустите с флагом --install-hg — утилита установит Mercurial в текущее
     окружение (pip install mercurial).
  2) Установите вручную и повторите запуск:
       любая ОС:  pip install mercurial
       macOS:     brew install mercurial
       Ubuntu:    sudo apt install mercurial
  3) Уберите Mercurial-репозитории из входного списка, если они не нужны.
"""


def hg_repo_sources(dataset_path: Path) -> List[str]:
    """Entries of the input that require Mercurial, in the order they appear.

    Handles all three input shapes: a repos.txt of URLs (``hg+`` prefix or a
    known Mercurial host), a directory of ``*.hgbundle`` files, and a directory
    of already-cloned working copies containing ``.hg``.
    """
    found: List[str] = []
    if dataset_path.is_file():
        if dataset_path.suffix.lower() != ".txt":
            return found
        try:
            lines = dataset_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return found
        for line in lines:
            url = line.strip()
            if not url or url.startswith("#"):
                continue
            if detect_vcs_from_url(url).name == "hg":
                found.append(url)
        return found

    if not dataset_path.is_dir():
        return found
    found.extend(sorted(str(p.name) for p in dataset_path.rglob("*.hgbundle")))
    for child in sorted(dataset_path.iterdir()):
        if child.is_dir() and (child / ".hg").exists():
            found.append(child.name)
    return found


def dataset_needs_hg(dataset_path: Path) -> bool:
    return bool(hg_repo_sources(dataset_path))


def install_hg() -> Optional[Path]:
    """Install Mercurial into the running interpreter's environment."""
    logger.info("Устанавливаю Mercurial: pip install mercurial")
    result = subprocess.run(
        [sys.executable, "-m", "pip", "install", "mercurial"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    if result.returncode != 0:
        logger.warning(
            "Не удалось установить Mercurial: %s",
            result.stderr.decode("utf-8", "replace").strip()[-400:],
        )
        return None
    found = shutil.which("hg")
    return Path(found) if found else None


def ensure_hg(dataset_path: Path, auto_install: bool = False) -> Optional[Path]:
    """Require ``hg`` only when the input actually contains Mercurial repos.

    Returns the binary path, None when Mercurial is not needed at all, and
    raises SystemExit with instructions when it is needed but missing.
    """
    sources = hg_repo_sources(dataset_path)
    if not sources:
        return None
    found = shutil.which("hg")
    if found:
        logger.info(
            "Найдено Mercurial-репозиториев во входных данных: %d (hg: %s)",
            len(sources), found,
        )
        return Path(found)
    if auto_install:
        installed = install_hg()
        if installed is not None:
            logger.info("Mercurial установлен: %s", installed)
            return installed
    logger.error(
        "Mercurial-репозитории во входных данных (%d), например: %s",
        len(sources), ", ".join(sources[:3]),
    )
    raise SystemExit(MISSING_MESSAGE)
