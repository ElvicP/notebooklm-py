"""Spec 0.4 — httpx/httpcore log redaction is on by default.

`httpx` logs from a single ``httpx`` logger; `httpcore` logs only from
*child* loggers (``httpcore.connection``, ``httpcore.http11``, ...) — there
is no bare ``httpcore`` logger that emits. So redaction must ride on a
handler attached to the ``httpcore`` parent (handlers run for ancestors
during propagation), not a logger-level filter (which would never run for
the child records). These tests pin:

- `configure_logging()` installs the redacting handler on ``httpx`` and
  ``httpcore`` by default.
- A child ``httpcore.connection`` record is scrubbed via the parent
  handler (the propagation path that a logger-only filter would miss).
- `NOTEBOOKLM_REDACT_HTTPX=0` (and other falsey spellings) opts out;
  unset / truthy keeps it on.
"""

from __future__ import annotations

import io
import logging

import pytest

from notebooklm._logging import (
    RedactingFilter,
    _redact_httpx_enabled,
    configure_logging,
)

_MARKER = "_notebooklm_redacting"


@pytest.fixture
def saved_external_logger():
    """Snapshot/restore arbitrary external loggers by name."""
    saved: dict[str, tuple] = {}

    def _save(name: str) -> logging.Logger:
        lg = logging.getLogger(name)
        saved[name] = (lg.handlers[:], lg.filters[:], lg.level, lg.propagate)
        lg.handlers.clear()
        lg.filters.clear()
        lg.setLevel(logging.WARNING)
        lg.propagate = True
        return lg

    yield _save
    for name, (h, f, lvl, p) in saved.items():
        lg = logging.getLogger(name)
        lg.handlers[:] = h
        lg.filters[:] = f
        lg.setLevel(lvl)
        lg.propagate = p


def _marked_handlers(logger: logging.Logger) -> list[logging.Handler]:
    return [h for h in logger.handlers if getattr(h, _MARKER, False)]


# ---------------------------------------------------------------------------
# Redaction on by default
# ---------------------------------------------------------------------------


def test_httpx_logger_redacted_by_default(saved_external_logger, monkeypatch):
    """Default (env unset): the httpx logger gets a redacting handler and a
    sensitive URL emitted at WARNING comes out scrubbed."""
    monkeypatch.delenv("NOTEBOOKLM_REDACT_HTTPX", raising=False)
    saved_external_logger("httpx")

    configure_logging()

    httpx_logger = logging.getLogger("httpx")
    ours = _marked_handlers(httpx_logger)
    assert len(ours) == 1, "configure_logging did not install a redacting handler on httpx"

    buf = io.StringIO()
    ours[0].stream = buf
    httpx_logger.warning("HTTP Request: GET https://x.example/v?at=SECRET_TOK&f.sid=SIDVAL")

    out = buf.getvalue()
    assert "SECRET_TOK" not in out
    assert "SIDVAL" not in out
    assert "at=***" in out
    assert "f.sid=***" in out


def test_httpcore_child_logger_redacted_via_parent_handler(saved_external_logger, monkeypatch):
    """httpcore emits only from child loggers (httpcore.connection, ...). The
    record must be scrubbed via the handler on the httpcore *parent* — the
    propagation path a logger-level filter would silently miss."""
    monkeypatch.delenv("NOTEBOOKLM_REDACT_HTTPX", raising=False)
    saved_external_logger("httpcore")
    saved_external_logger("httpcore.connection")

    configure_logging()

    httpcore_logger = logging.getLogger("httpcore")
    ours = _marked_handlers(httpcore_logger)
    assert len(ours) == 1, "configure_logging did not install a redacting handler on httpcore"

    buf = io.StringIO()
    ours[0].stream = buf
    logging.getLogger("httpcore.connection").warning(
        "connect_tcp: Cookie: SAPISID=leaky_cookie_val; SID=sid_secret"
    )

    out = buf.getvalue()
    assert "leaky_cookie_val" not in out
    assert "sid_secret" not in out
    assert "Cookie: ***" in out


def test_default_handler_carries_redacting_filter(saved_external_logger, monkeypatch):
    """Structural: the installed httpx handler has the RedactingFilter."""
    monkeypatch.delenv("NOTEBOOKLM_REDACT_HTTPX", raising=False)
    saved_external_logger("httpx")

    configure_logging()

    handler = _marked_handlers(logging.getLogger("httpx"))[0]
    assert any(isinstance(f, RedactingFilter) for f in handler.filters)


# ---------------------------------------------------------------------------
# Opt-out env var
# ---------------------------------------------------------------------------


def test_opt_out_env_disables_httpx_redaction(saved_external_logger, monkeypatch):
    """NOTEBOOKLM_REDACT_HTTPX=0 → no redacting handler is installed on httpx,
    so a sensitive record would pass through un-scrubbed."""
    monkeypatch.setenv("NOTEBOOKLM_REDACT_HTTPX", "0")
    saved_external_logger("httpx")
    saved_external_logger("httpcore")

    configure_logging()

    assert _marked_handlers(logging.getLogger("httpx")) == []
    assert _marked_handlers(logging.getLogger("httpcore")) == []


@pytest.mark.parametrize("value", ["0", "false", "False", "no", "off", " 0 "])
def test_redact_httpx_disabled_for_falsey_values(value, monkeypatch):
    monkeypatch.setenv("NOTEBOOKLM_REDACT_HTTPX", value)
    assert _redact_httpx_enabled() is False


@pytest.mark.parametrize("value", ["1", "true", "yes", "on", "anything-else"])
def test_redact_httpx_enabled_for_truthy_values(value, monkeypatch):
    monkeypatch.setenv("NOTEBOOKLM_REDACT_HTTPX", value)
    assert _redact_httpx_enabled() is True


def test_redact_httpx_enabled_by_default_when_unset(monkeypatch):
    monkeypatch.delenv("NOTEBOOKLM_REDACT_HTTPX", raising=False)
    assert _redact_httpx_enabled() is True
