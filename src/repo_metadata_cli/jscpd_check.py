"""Fail-fast check for the external `jscpd` binary.

jscpd computes duplication_ratio and meta_duplication_ratio.  When the binary
is missing both metrics silently become 0.0, which is indistinguishable from a
repository that genuinely has no clones — the gap only surfaces later, when a
delivered CSV shows 0.00 duplication for repositories with millions of lines.
The metadata command now refuses to start without jscpd; pass
`--allow-missing-jscpd` to run anyway and accept zeroed duplication columns.
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

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


def ensure_jscpd(allow_missing: bool = False) -> Optional[Path]:
    """Return the jscpd binary path, or exit with instructions when missing.

    Returns None when the binary is missing and the caller opted out of the
    check.  SystemExit carries the human-readable message: Python prints it to
    stderr and exits with code 1, so partners see the instruction, not a
    traceback.
    """
    found = shutil.which("jscpd")
    if found:
        return Path(found)
    if allow_missing:
        logger.warning(
            "jscpd не найден, расчёт продолжается по --allow-missing-jscpd: "
            "duplication_ratio и meta_duplication_ratio будут нулевыми."
        )
        return None
    raise SystemExit(MISSING_MESSAGE)
