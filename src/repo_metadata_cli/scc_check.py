"""Fail-fast check for the external `scc` binary.

scc computes every LOC/language column (raw_loc, logical_loc, source_files,
lang_distribution, extensions, comment_ratio, symbols_count, …).  When the
binary was missing the pipeline still ran and silently wrote zeros into all
of those columns — the problem only surfaced after the CSV was delivered.
The metadata command now refuses to start without scc; `--install-scc`
downloads the official release binary into the current environment.
"""

from __future__ import annotations

import logging
import os
import platform
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_RELEASE_URL = "https://github.com/boyter/scc/releases/latest/download/scc_{os}_{arch}.tar.gz"

_OS_NAMES = {"linux": "Linux", "darwin": "Darwin"}
_ARCH_NAMES = {"x86_64": "x86_64", "amd64": "x86_64", "arm64": "arm64", "aarch64": "arm64"}

MISSING_MESSAGE = """\
Не найден scc — внешняя утилита подсчёта строк кода.

Без scc все LOC-метрики (raw_loc, logical_loc, source_files, языковые
распределения и т.д.) получились бы нулевыми, поэтому расчёт не запущен.

Варианты:
  1) Запустите с флагом --install-scc — утилита сама скачает официальный
     релиз scc и установит его в текущее окружение.
  2) Установите вручную и повторите запуск:
       macOS:  brew install scc
       Linux:  curl -L https://github.com/boyter/scc/releases/latest/download/scc_Linux_x86_64.tar.gz | tar xz
               sudo mv scc /usr/local/bin/
"""


def _release_url() -> str:
    os_name = _OS_NAMES.get(sys.platform)
    arch = _ARCH_NAMES.get(platform.machine().lower())
    if not os_name or not arch:
        raise RuntimeError(
            f"Автоустановка scc не поддерживается для {sys.platform}/{platform.machine()} — "
            "установите scc вручную (см. README, шаг 1)."
        )
    return _RELEASE_URL.format(os=os_name, arch=arch)


def install_scc(target_dir: Optional[Path] = None) -> Path:
    """Download the official scc release and put the binary into target_dir.

    Default target is the running interpreter's bin directory — inside the
    project venv it is already on PATH, so no shell profile changes needed.
    """
    target_dir = target_dir or Path(sys.executable).parent
    url = _release_url()
    logger.info("Скачиваю scc: %s", url)
    target = target_dir / "scc"
    with tempfile.TemporaryDirectory() as td:
        archive = Path(td) / "scc.tar.gz"
        urllib.request.urlretrieve(url, archive)
        with tarfile.open(archive, "r:gz") as tar:
            member = next(
                (m for m in tar.getmembers() if m.isfile() and Path(m.name).name == "scc"),
                None,
            )
            if member is None:
                raise RuntimeError("В архиве релиза scc не найден бинарник scc.")
            src = tar.extractfile(member)
            assert src is not None
            target.write_bytes(src.read())
    target.chmod(target.stat().st_mode | 0o755)
    logger.info("scc установлен: %s", target)
    return target


def ensure_scc(auto_install: bool = False) -> Path:
    """Return the scc binary path, installing it or exiting with instructions.

    SystemExit carries the human-readable message: Python prints it to stderr
    and exits with code 1, so partners see the instruction, not a traceback.
    """
    found = shutil.which("scc")
    if found:
        return Path(found)
    if auto_install:
        target = install_scc()
        if target.exists() and os.access(target, os.X_OK):
            return target
    raise SystemExit(MISSING_MESSAGE)
