"""General helper utilities used across the CLI."""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path
from typing import List, Optional

LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def configure_logging(log_level: str = "INFO") -> None:
    """
    Configure root logging with a concise format.
    Reconfigures only once per process to avoid duplicate handlers.
    """
    level = log_level.upper()
    numeric_level = logging.getLevelName(level)
    if not isinstance(numeric_level, int):
        numeric_level = logging.INFO

    logging.basicConfig(
        level=numeric_level,
        format=LOG_FORMAT,
        force=True,
    )
    # Keep noisy third-party libraries quieter by default.
    logging.getLogger("transformers").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def run_cmd(cmd: List[str], cwd: Optional[Path] = None, timeout: int = 720) -> str:
    """Execute a shell command and return stdout as text. Returns an empty string on error.

    timeout: seconds before the process is killed (default 120 s).  Prevents
    hanging indefinitely on very large repositories or slow external tools.
    """
    try:
        result = subprocess.check_output(
            cmd,
            cwd=str(cwd) if cwd else None,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
        return result.decode("utf-8", errors="replace").strip()
    except subprocess.TimeoutExpired:
        logging.getLogger(__name__).warning(
            "Command timed out after %ds: %s", timeout, " ".join(cmd)
        )
        return ""
    except (subprocess.CalledProcessError, FileNotFoundError, OSError) as exc:
        logging.getLogger(__name__).debug("Command failed: %s (%s)", " ".join(cmd), exc)
        return ""


def is_utf8_file(path: Path, sample_size: int = 4096) -> bool:
    """Heuristic check that a file is UTF-8 text (not binary)."""
    try:
        with path.open("rb") as fh:
            chunk = fh.read(sample_size)
    except OSError:
        return False

    if not chunk:
        return False

    try:
        chunk.decode("utf-8")
        return True
    except UnicodeDecodeError as exc:
        # A full-size sample may split a multibyte character at its end (a
        # UTF-8 code point is up to 4 bytes) — that is not evidence of a
        # binary file.  A short read reached EOF, so there the error is real.
        return len(chunk) == sample_size and exc.start >= len(chunk) - 3
