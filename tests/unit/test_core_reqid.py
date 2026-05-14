"""Unit tests for ``ClientCore.next_reqid`` and the deprecation guard on
direct mutation of ``_reqid_counter``.

Covers PR-T2.A of the Tier-2 remediation:
- ``next_reqid()`` returns monotonic, post-increment values.
- Custom ``step`` parameter works.
- ``DeprecationWarning`` is emitted on ``core._reqid_counter = ...`` and on
  ``core._reqid_counter += ...``.
- ``next_reqid()`` itself does NOT emit a ``DeprecationWarning``.
"""

import warnings

import pytest

from notebooklm._core import ClientCore
from notebooklm.auth import AuthTokens


def _make_core() -> ClientCore:
    auth = AuthTokens(
        cookies={"SID": "test"},
        csrf_token="test_csrf",
        session_id="test_session",
    )
    return ClientCore(auth=auth)


@pytest.mark.asyncio
async def test_next_reqid_returns_post_increment_values() -> None:
    """Three successive calls bump by the default step and return new values."""
    core = _make_core()
    assert core._reqid_counter == 100000  # baseline

    first = await core.next_reqid()
    second = await core.next_reqid()
    third = await core.next_reqid()

    assert first == 200000
    assert second == 300000
    assert third == 400000
    # And the property reflects the final state.
    assert core._reqid_counter == 400000


@pytest.mark.asyncio
async def test_next_reqid_custom_step() -> None:
    """A non-default ``step`` parameter is honoured."""
    core = _make_core()
    assert await core.next_reqid(step=1) == 100001
    assert await core.next_reqid(step=7) == 100008
    assert await core.next_reqid(step=1000) == 101008


@pytest.mark.asyncio
async def test_next_reqid_does_not_warn() -> None:
    """The intended API surface must be silent — no ``DeprecationWarning``."""
    core = _make_core()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        await core.next_reqid()
        await core.next_reqid()
    deprecations = [w for w in caught if issubclass(w.category, DeprecationWarning)]
    assert deprecations == [], (
        "next_reqid() must not emit DeprecationWarning; "
        f"got {[(w.category.__name__, str(w.message)) for w in deprecations]}"
    )


def test_direct_assignment_warns() -> None:
    """``core._reqid_counter = N`` must emit a ``DeprecationWarning``."""
    core = _make_core()
    with pytest.warns(DeprecationWarning, match="next_reqid"):
        core._reqid_counter = 0
    # Setter still applies the value (backwards compatible).
    assert core._reqid_counter == 0


def test_read_modify_write_warns() -> None:
    """``core._reqid_counter += step`` must warn — this is the existing
    ``_chat.py`` pattern that T2.D will migrate.
    """
    core = _make_core()
    with pytest.warns(DeprecationWarning, match="next_reqid"):
        core._reqid_counter += 100000
    assert core._reqid_counter == 200000
