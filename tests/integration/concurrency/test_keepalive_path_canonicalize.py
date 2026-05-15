"""T7.G6 — Canonicalize keepalive storage path for in-process dedupe.

Regression test for audit §29: the keepalive rotation throttle keys the
in-process dedupe (``_LAST_POKE_ATTEMPT_MONOTONIC`` /
``_POKE_LOCKS_BY_LOOP``) by the raw ``Path`` object stored on
``ClientCore._keepalive_storage_path``. Without canonicalization, two
clients constructed with different syntactic representations of the SAME
underlying file (e.g. a relative path and the absolute path; a
``~``-prefixed path and the expanded one; with or without a symlink
component) hash to distinct dict keys and bypass the dedupe entirely —
duplicate ``RotateCookies`` POSTs.

``auth.py:_fetch_tokens_with_refresh`` already canonicalizes via
``Path(p).expanduser().resolve()`` before keying the refresh
generation/lock. ``NotebookLMClient`` must do the same for the keepalive
path before it reaches ``_get_poke_lock`` / ``_try_claim_rotation`` /
``_rotation_lock_path``.

The public ``storage_path`` argument type (``str | Path | None``) is
preserved; only the internal-derived ``ClientCore._keepalive_storage_path``
is canonicalized.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from notebooklm import NotebookLMClient
from notebooklm.auth import AuthTokens


@pytest.fixture
def _auth_tokens() -> AuthTokens:
    """Auth with no storage_path baked in; tests supply their own per-case."""
    return AuthTokens(
        cookies={"SID": "test_sid"},
        csrf_token="test_csrf",
        session_id="test_session",
    )


def test_relative_and_absolute_paths_share_dedupe_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _auth_tokens: AuthTokens
) -> None:
    """Two clients built with relative vs absolute paths to the SAME file
    must hold the same canonical ``_keepalive_storage_path``.

    Hashing the canonical ``Path`` is what gates the
    ``_LAST_POKE_ATTEMPT_MONOTONIC`` dedupe key. If the two clients store
    different ``Path`` objects, the dedupe registry treats them as
    independent profiles and both fire ``RotateCookies`` inside the
    rate-limit window.
    """
    # Real on-disk target. ``.resolve()`` requires the path to exist on
    # macOS+strict for portability across runners, but ``Path.resolve()``
    # tolerates non-existent paths on Linux. Create the file so both
    # platforms agree.
    target = tmp_path / "storage_state.json"
    target.write_text("{}", encoding="utf-8")

    absolute_path = target.resolve()

    # Build a RELATIVE representation of the same file by chdir-ing into
    # tmp_path and using just the filename. This is the canonical
    # "relative vs absolute" footgun from audit §29.
    monkeypatch.chdir(tmp_path)
    relative_path = Path("storage_state.json")

    # Sanity: the two Path objects must differ as raw values, otherwise
    # the test below wouldn't actually be exercising canonicalization.
    assert relative_path != absolute_path
    assert str(relative_path) != str(absolute_path)

    client_rel = NotebookLMClient(_auth_tokens, storage_path=relative_path)
    client_abs = NotebookLMClient(_auth_tokens, storage_path=absolute_path)

    # The dedupe key seen by ``_get_poke_lock`` / ``_try_claim_rotation``
    # / ``_rotation_lock_path`` is exactly this internal field. Both must
    # be canonical and equal.
    rel_keepalive = client_rel._core._keepalive_storage_path
    abs_keepalive = client_abs._core._keepalive_storage_path
    assert rel_keepalive is not None
    assert abs_keepalive is not None
    assert rel_keepalive == abs_keepalive, (
        f"Keepalive dedupe key differs across path representations: "
        f"{rel_keepalive!r} != {abs_keepalive!r}"
    )
    # Canonical form: absolute, expanduser-applied, symlinks resolved.
    assert rel_keepalive.is_absolute()
    assert rel_keepalive == absolute_path


def test_tilde_path_is_expanded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, _auth_tokens: AuthTokens
) -> None:
    """A ``~``-prefixed path must expand to the same canonical form as
    its expanded sibling — second representation flavor from audit §29.
    """
    # Pretend HOME lives under tmp_path so ``~`` expansion is hermetic.
    monkeypatch.setenv("HOME", str(tmp_path))
    target = tmp_path / "auth.json"
    target.write_text("{}", encoding="utf-8")

    tilde_path = Path("~/auth.json")
    expanded_path = (tmp_path / "auth.json").resolve()

    client_tilde = NotebookLMClient(_auth_tokens, storage_path=tilde_path)
    client_expanded = NotebookLMClient(_auth_tokens, storage_path=expanded_path)

    tilde_key = client_tilde._core._keepalive_storage_path
    expanded_key = client_expanded._core._keepalive_storage_path
    assert tilde_key is not None
    assert expanded_key is not None
    assert tilde_key == expanded_key
    assert tilde_key == expanded_path


def test_public_storage_path_argument_unchanged(tmp_path: Path, _auth_tokens: AuthTokens) -> None:
    """T7.G6 must preserve the public path argument type (``str | Path |
    None``) — only the internal-derived ``_keepalive_storage_path`` is
    canonicalized. The auth object's ``storage_path`` and the argument
    passed by the caller stay as-is so external observers (logs,
    serialization, downstream callers) see the original value.
    """
    target = tmp_path / "store.json"
    target.write_text("{}", encoding="utf-8")
    # An unresolved path (no .resolve() applied) — the relative-from-cwd
    # case isn't portable here, so use an absolute Path that lacks
    # canonicalization side effects but is well-formed.
    raw_path = Path(str(target))

    client = NotebookLMClient(_auth_tokens, storage_path=raw_path)

    # auth.storage_path was normalized onto the auth object by __init__
    # (see client.py:153-154). The PUBLIC value remains the caller's
    # original Path — not the canonicalized one.
    assert client.auth.storage_path == raw_path
    # And the internal keepalive field IS canonicalized.
    keepalive_key = client._core._keepalive_storage_path
    assert keepalive_key is not None
    assert keepalive_key == target.resolve()


def test_none_storage_path_stays_none(_auth_tokens: AuthTokens) -> None:
    """Canonicalization must be a no-op when there is no storage path."""
    client = NotebookLMClient(_auth_tokens)
    assert client._core._keepalive_storage_path is None
