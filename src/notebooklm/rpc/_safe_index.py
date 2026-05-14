"""Shared schema-drift helper for indexing into decoded RPC payloads.

``safe_index`` walks a nested list/tuple by integer keys with soft-strict
semantics. In the default soft-rollout mode it logs a warning and returns
``None`` on drift; setting ``NOTEBOOKLM_STRICT_DECODE=1`` flips it to raise
``UnknownRPCMethodError`` so callers fail fast when Google's response shape
moves out from under us.

This is the single shared point of policy for "the payload didn't look like
we expected" — call sites should migrate to ``safe_index`` rather than
hand-rolling ``try/except IndexError`` blocks.
"""

from __future__ import annotations

import logging
from typing import Any

from .._env import is_strict_decode_enabled
from ..exceptions import UnknownRPCMethodError

__all__ = ["safe_index"]

logger = logging.getLogger(__name__)

_REPR_TRUNCATE = 200


def _truncate(value: Any) -> str:
    """Return a length-bounded repr suitable for logs/exception attributes."""
    text = repr(value)
    if len(text) <= _REPR_TRUNCATE:
        return text
    return text[:_REPR_TRUNCATE] + "..."


def safe_index(
    data: Any,
    *path: int,
    method_id: str | int | None,
    source: str,
) -> Any:
    """Walk ``data`` by ``path`` indices with soft-strict drift handling.

    Args:
        data: Nested list/tuple structure (typically a decoded RPC payload).
        *path: Sequence of integer indices to descend.
        method_id: RPC method ID (for diagnostics on drift).
        source: Caller label identifying where the drift was observed
            (e.g. ``"_notebooks.list"``); included in logs and the raised
            exception's ``source`` attribute.

    Returns:
        The value at ``data[path[0]][path[1]]...`` on success, or ``None`` in
        soft mode when descent fails.

    Raises:
        UnknownRPCMethodError: When ``NOTEBOOKLM_STRICT_DECODE`` is truthy and
            descent fails. The exception carries ``method_id``, ``source``,
            ``path`` (truncated to where descent stopped), and a truncated
            ``data_at_failure`` repr.
    """
    current: Any = data
    for i, key in enumerate(path):
        try:
            current = current[key]
        except (IndexError, TypeError, KeyError) as exc:
            failing_path = tuple(path[:i])
            data_repr = _truncate(current)
            if is_strict_decode_enabled():
                raise UnknownRPCMethodError(
                    f"safe_index drift at path {failing_path}[{key}] "
                    f"(method_id={method_id!r}, source={source!r})",
                    method_id=method_id,
                    path=failing_path,
                    source=source,
                    data_at_failure=data_repr,
                ) from exc
            logger.warning(
                "safe_index drift at %r[%d] (method_id=%r, source=%r): %s",
                failing_path,
                key,
                method_id,
                source,
                data_repr,
            )
            return None
    return current
