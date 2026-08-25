"""Fail-fast check for the external `jscpd` binary.

jscpd computes duplication_ratio and meta_duplication_ratio.  When it does not
work both metrics silently become 0.0, which is indistinguishable from a
repository that genuinely has no clones — the gap only surfaces later, when a
delivered CSV shows 0.00 duplication for repositories with millions of lines.
The metadata command refuses to start without a working jscpd; pass
`--allow-missing-jscpd` to run anyway and accept zeroed duplication columns.

Presence on PATH is not enough: jscpd is an npm package whose bin script starts
with `#!/usr/bin/env node`, so without Node.js on PATH every invocation exits
with 127 while `shutil.which` still reports a hit.  The check therefore runs
`jscpd --version` and requires a zero exit code — the same thing the pipeline
will do later, only cheaper and before any repository is processed.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_VERSION_TIMEOUT_SECONDS = 60

MISSING_MESSAGE = """\
Не найден jscpd — внешняя утилита поиска дублирования кода.

Без jscpd колонки duplication_ratio и meta_duplication_ratio получились бы
нулевыми и были бы неотличимы от репозиториев без дублей, поэтому расчёт
не запущен.

Варианты:
  1) Установите jscpd (нужен Node.js) и повторите запуск:
       npm install -g jscpd
  2) Запустите с флагом --allow-missing-jscpd, если дупликация не нужна:
     обе колонки будут нулевыми осознанно.
"""

BROKEN_MESSAGE = """\
Найден jscpd ({path}), но запустить его не удалось: {reason}.

Без работающего jscpd колонки duplication_ratio и meta_duplication_ratio
получились бы нулевыми и были бы неотличимы от репозиториев без дублей,
поэтому расчёт не запущен.
{hint}
Проверьте вручную — команда должна завершиться с кодом 0:
  jscpd --version

Либо запустите с флагом --allow-missing-jscpd, если дупликация не нужна:
обе колонки будут нулевыми осознанно.
"""

NODE_HINT = """
Код 127 означает, что не найден node: jscpd — npm-пакет, его bin-скрипт
запускается через Node.js. Проверьте в этой же оболочке:
  which node && node --version
Если node виден только в интерактивном терминале — так ведёт себя nvm, он
подключается из ~/.bashrc. Поставьте Node системным пакетом либо запускайте
расчёт в оболочке, где nvm уже загружен.
"""


def _smoke_failure(path: str) -> Optional[str]:
    """Return why `jscpd --version` failed, or None when it works."""
    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=_VERSION_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"команда не ответила за {_VERSION_TIMEOUT_SECONDS}с"
    except OSError as exc:
        return f"не удалось запустить процесс ({exc})"
    if result.returncode == 0:
        return None
    detail = (result.stderr or result.stdout or "").strip().splitlines()
    suffix = f" — {detail[0]}" if detail else ""
    return f"код возврата {result.returncode}{suffix}"


def ensure_jscpd(allow_missing: bool = False) -> Optional[Path]:
    """Return the jscpd binary path, or exit with instructions when unusable.

    The binary is both looked up on PATH and actually executed once, because a
    hit from `shutil.which` says nothing about whether the npm bin script can
    start.  Returns None when jscpd does not work and the caller opted out of
    the check.  SystemExit carries the human-readable message: Python prints it
    to stderr and exits with code 1, so partners see the instruction, not a
    traceback.
    """
    found = shutil.which("jscpd")
    if found:
        failure = _smoke_failure(found)
        if failure is None:
            return Path(found)
        if allow_missing:
            logger.warning(
                "jscpd не работает (%s), расчёт продолжается по --allow-missing-jscpd: "
                "duplication_ratio и meta_duplication_ratio будут нулевыми.",
                failure,
            )
            return None
        hint = NODE_HINT if "127" in failure else ""
        raise SystemExit(BROKEN_MESSAGE.format(path=found, reason=failure, hint=hint))
    if allow_missing:
        logger.warning(
            "jscpd не найден, расчёт продолжается по --allow-missing-jscpd: "
            "duplication_ratio и meta_duplication_ratio будут нулевыми."
        )
        return None
    raise SystemExit(MISSING_MESSAGE)
