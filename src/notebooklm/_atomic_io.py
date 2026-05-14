"""Atomic JSON write helpers.

Shared by :mod:`notebooklm.auth` and :mod:`notebooklm.cli.session` so both
write sites for ``storage_state.json`` use the same crash- and concurrency-safe
pattern (NamedTemporaryFile in the same directory, ``chmod 0o600``, then
``os.replace``).

Default permission mode is ``0o600`` because the primary caller writes
Playwright storage state containing session cookies, which are credential-
equivalent secrets.

For read-modify-write workflows on shared JSON state (``context.json``,
``config.json``), use :func:`atomic_update_json` — it wraps the read, mutate,
and atomic write inside a cross-process file lock (via the ``filelock``
library) so that two concurrent CLI invocations never lose updates.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from filelock import FileLock

logger = logging.getLogger(__name__)


def atomic_write_json(path: Path, data: Any, *, mode: int = 0o600) -> None:
    """Write ``data`` as JSON to ``path`` atomically.

    Steps:

    1. Serialize ``data`` to a sibling :class:`tempfile.NamedTemporaryFile` in
       the same directory as ``path`` (same-filesystem for ``os.replace``
       atomicity).
    2. ``chmod`` the temp file to ``mode`` (default ``0o600`` — cookies are
       secrets). Skipped on Windows where POSIX permissions are a no-op and
       can confuse ACLs.
    3. ``os.replace`` the temp file onto ``path`` (atomic on POSIX and Windows).
    4. On any failure: unlink the temp file and re-raise.

    The caller decides whether to log/swallow the exception.
    """

    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temp_file:
            # Capture temp path BEFORE write so cleanup-on-failure can still
            # unlink it if write() raises (e.g. ENOSPC, EROFS). Without this,
            # partial temp files would leak into the storage parent dir on
            # every failed save attempt.
            temp_path = Path(temp_file.name)
            json.dump(data, temp_file, indent=2, ensure_ascii=False)
        if sys.platform != "win32":
            # chmod is a no-op on Windows (and can confuse ACLs)
            os.chmod(temp_path, mode)
        os.replace(temp_path, path)
    except Exception:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except Exception as cleanup_err:
                logger.debug("Failed to clean up temp file %s: %s", temp_path, cleanup_err)
        raise


def atomic_update_json(
    path: Path,
    mutator: Callable[[dict[str, Any]], dict[str, Any]],
    *,
    mode: int = 0o600,
    timeout: float = 10.0,
) -> None:
    """Lock + read + mutate + atomic write of a JSON file.

    Acquires a sibling ``<path>.lock`` file via :class:`filelock.FileLock`
    (cross-platform: POSIX + macOS + Windows), reads the current JSON contents
    (or an empty dict if the file does not exist), passes them to ``mutator``,
    and writes the result back via :func:`atomic_write_json`.

    The lock is held across the entire read-modify-write sequence so that
    two concurrent CLI invocations cannot lose updates by writing stale
    snapshots over each other. The default ``timeout`` is generous enough to
    survive normal contention but bounded so a crashed holder cannot wedge
    the next caller forever — exceeding it raises :class:`filelock.Timeout`.

    Args:
        path: Target JSON file. Parent directory is created if missing.
        mutator: Pure function ``current -> updated``. Must return a dict.
            Callers may mutate ``current`` in place and return it.
        mode: POSIX permission bits for the written file (default ``0o600``).
        timeout: Seconds to wait for the lock before raising.

    Raises:
        filelock.Timeout: If the lock cannot be acquired within ``timeout``.
        json.JSONDecodeError: If the existing file is not valid JSON. Callers
            wanting recovery-on-corruption should pre-clean the file.
        OSError: From filesystem operations (mkdir, write, replace).
    """
    lock_path = path.with_suffix(path.suffix + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(str(lock_path), timeout=timeout):
        if path.exists():
            current = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(current, dict):
                current = {}
        else:
            current = {}
        updated = mutator(current)
        atomic_write_json(path, updated, mode=mode)
